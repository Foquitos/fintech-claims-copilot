"""Agent graph tests with a fake LLM: no network, no tokens."""
import pytest
from pydantic import ValidationError

from app import prompts
from app.agent.graph import CLASSIFY_PROMPT, build_graph
from app.agent.state import Action, Category, Classification
from app.llm import LLMOutputError, get_client


class FakeLLM:
    """Returns queued replies in order (an Exception instance is raised) and records calls."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[dict] = []

    def generate(self, system, user, schema):
        self.calls.append({"system": system, "user": user, "schema": schema})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def run(llm, claim="Me cobraron dos veces las zapatillas", customer_id="C0001"):
    return build_graph(llm).invoke({"claim": claim, "customer_id": customer_id})


def test_classify_sets_category_and_prompt_version():
    llm = FakeLLM(Classification(category=Category.DUPLICATE_CHARGE, confidence=0.93))
    out = run(llm)

    assert out["category"] == Category.DUPLICATE_CHARGE
    assert out["confidence"] == 0.93
    assert "action" not in out  # in-scope claims continue (to RF-002 nodes from week 2)
    prompt = prompts.load(CLASSIFY_PROMPT)
    assert out["prompt_versions"] == {"classify": f"classify.v1@{prompt.sha}"}
    assert llm.calls[0]["system"] == prompt.text
    assert llm.calls[0]["user"] == "Me cobraron dos veces las zapatillas"


def test_other_category_escalates_without_tools():
    # CA-001.2: off-topic claims are "other" and no tool runs. The fake has a single reply,
    # so any extra model call would fail the test.
    llm = FakeLLM(Classification(category=Category.OTHER, confidence=0.99))
    out = run(llm, claim="¿Qué hora es?")

    assert out["category"] == Category.OTHER
    assert out["action"] == Action.ESCALATE_OPERATIONS
    assert len(llm.calls) == 1


@pytest.mark.parametrize("error", [LLMOutputError("refused"),
                                   ValidationError.from_exception_data("Classification", [])])
def test_unusable_output_falls_back_to_human(error):
    out = run(FakeLLM(error))

    assert out["category"] == Category.OTHER
    assert out["confidence"] == 0.0
    assert out["action"] == Action.ESCALATE_OPERATIONS
    assert out["errors"] and out["errors"][0].startswith("classify:")


def test_provider_failure_propagates():
    # Timeouts must surface as a 503 upstream, not as a made-up classification.
    with pytest.raises(TimeoutError):
        run(FakeLLM(TimeoutError("provider timed out")))


def test_classification_schema_bounds_confidence():
    with pytest.raises(ValidationError):
        Classification(category=Category.CASHBACK, confidence=1.5)


def test_prompt_lists_every_category():
    text = prompts.load(CLASSIFY_PROMPT).text
    assert all(f"- {c.value}:" in text for c in Category)


def test_client_targets_azure_v1_endpoint(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
    assert str(get_client().base_url) == "https://example.openai.azure.com/openai/v1/"


def test_client_defaults_to_openai(monkeypatch):
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    assert "api.openai.com" in str(get_client().base_url)
