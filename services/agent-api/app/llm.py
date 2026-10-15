"""LLM access.

One OpenAI client serves both providers. Azure OpenAI exposes an OpenAI-compatible v1 API at
<endpoint>/openai/v1/, so only base_url and api_key change; the model name is the Azure
deployment name. Without AZURE_OPENAI_ENDPOINT the client talks to OpenAI directly.

The graph depends on the small StructuredLLM protocol, not on the SDK, so tests can inject a
fake model and spend no tokens.
"""
import os
from typing import Protocol, TypeVar

from openai import OpenAI
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

TIMEOUT_S = 20  # PRD 9.1.6: provider down or slow -> 503 to the operator, no silent waits


class LLMOutputError(Exception):
    """The model refused or returned nothing parseable."""


def get_client() -> OpenAI:
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    if endpoint:
        return OpenAI(base_url=endpoint.rstrip("/") + "/openai/v1/",
                      api_key=os.environ["AZURE_OPENAI_API_KEY"], timeout=TIMEOUT_S)
    return OpenAI(timeout=TIMEOUT_S)


class StructuredLLM(Protocol):
    def generate(self, system: str, user: str, schema: type[T]) -> T: ...


class OpenAIStructuredLLM:
    """Structured outputs: the API constrains the reply to the JSON schema of `schema`."""

    def __init__(self, model: str | None = None, client: OpenAI | None = None) -> None:
        self.model = model or os.getenv("LLM_MODEL", "gpt-4.1-mini")
        self.client = client or get_client()
        # Running token totals, read by eval scripts to estimate cost. Langfuse replaces this in F4.
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0}

    def generate(self, system: str, user: str, schema: type[T]) -> T:
        resp = self.client.chat.completions.parse(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format=schema,
        )
        self.usage["calls"] += 1
        if resp.usage:  # output tokens include reasoning tokens on reasoning models
            self.usage["input_tokens"] += resp.usage.prompt_tokens
            self.usage["output_tokens"] += resp.usage.completion_tokens
        msg = resp.choices[0].message
        if msg.parsed is None:
            raise LLMOutputError(msg.refusal or "empty response")
        return msg.parsed
