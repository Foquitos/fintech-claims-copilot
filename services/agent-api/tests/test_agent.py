"""Agent graph tests with a fake LLM and a mocked core-mock: no network, no tokens."""
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from app import prompts
from app.agent.graph import CLASSIFY_PROMPT, build_graph
from app.agent.state import Action, Category, Classification
from app.llm import LLMOutputError, get_client
from app.rag.chunking import load_policies
from app.rag.embeddings import HashEmbedder
from app.rag.index import PolicyIndex
from app.tools.core import CoreClient

ROOT = Path(__file__).resolve().parents[1]

CUSTOMERS = {"C0001": {"id": "C0001", "full_name": "Lucía Fernández", "dni_masked": "*****222",
                       "segment": "standard", "created_at": "2024-04-10T12:00:00",
                       "card_blocked": False}}
TRANSACTIONS = {"C0001": [
    {"id": "T0002", "customer_id": "C0001", "created_at": "2026-09-28T15:30:00",
     "type": "card_purchase", "status": "approved", "amount": "45999.00",
     "counterparty": "Zapatillas Run", "description": "Compra con tarjeta"},
    {"id": "T0001", "customer_id": "C0001", "created_at": "2026-09-28T15:28:00",
     "type": "card_purchase", "status": "approved", "amount": "45999.00",
     "counterparty": "Zapatillas Run", "description": "Compra con tarjeta"},
]}


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


class FakeCore:
    """A real CoreClient over a mock transport that serves CUSTOMERS/TRANSACTIONS."""

    def __init__(self, down: bool = False):
        self.requests: list[str] = []
        self.down = down
        self.client = CoreClient("http://core", transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(f"{request.method} {request.url.path}")
        if self.down:
            raise httpx.ConnectTimeout("core-mock timed out", request=request)
        parts = request.url.path.strip("/").split("/")
        if parts[0] == "customers" and parts[1] in CUSTOMERS:
            if len(parts) == 2:
                return httpx.Response(200, json=CUSTOMERS[parts[1]])
            return httpx.Response(200, json=TRANSACTIONS[parts[1]])
        return httpx.Response(404, json={"detail": "Customer not found"})


@pytest.fixture(scope="module")
def index():
    return PolicyIndex.build(load_policies(ROOT / "policies"), HashEmbedder())


@pytest.fixture
def run(index):
    def _run(llm, claim="Me cobraron dos veces las zapatillas", customer_id="C0001",
             core=None):
        core = core or FakeCore()
        return build_graph(llm, index, core.client).invoke(
            {"claim": claim, "customer_id": customer_id})
    return _run


def test_classify_sets_category_and_prompt_version(run):
    llm = FakeLLM(Classification(category=Category.DUPLICATE_CHARGE, confidence=0.93))
    out = run(llm)

    assert out["category"] == Category.DUPLICATE_CHARGE
    assert out["confidence"] == 0.93
    assert "action" not in out  # in-scope claims go on to retrieval and lookup
    prompt = prompts.load(CLASSIFY_PROMPT)
    assert out["prompt_versions"] == {"classify": f"classify.v1@{prompt.sha}"}
    assert llm.calls[0]["system"] == prompt.text
    assert llm.calls[0]["user"] == "Me cobraron dos veces las zapatillas"


def test_other_category_escalates_without_tools(run):
    # CA-001.2: off-topic claims are "other" and no tool runs. The fake has a single reply,
    # so any extra model call would fail the test.
    llm, core = FakeLLM(Classification(category=Category.OTHER, confidence=0.99)), FakeCore()
    out = run(llm, claim="¿Qué hora es?", core=core)

    assert out["category"] == Category.OTHER
    assert out["action"] == Action.ESCALATE_OPERATIONS
    assert len(llm.calls) == 1
    assert core.requests == []
    assert "policies" not in out and not out["tool_calls"]


@pytest.mark.parametrize("error", [LLMOutputError("refused"),
                                   ValidationError.from_exception_data("Classification", [])])
def test_unusable_output_falls_back_to_human(run, error):
    out = run(FakeLLM(error))

    assert out["category"] == Category.OTHER
    assert out["confidence"] == 0.0
    assert out["action"] == Action.ESCALATE_OPERATIONS
    assert out["errors"] and out["errors"][0].startswith("classify:")


def test_provider_failure_propagates(run):
    # Timeouts must surface as a 503 upstream, not as a made-up classification.
    with pytest.raises(TimeoutError):
        run(FakeLLM(TimeoutError("provider timed out")))


def test_duplicate_charge_retrieves_policy_and_checks_transactions(run, index):
    # AC-003.1: C0001's trace shows the transactions tool, and both charges reach the state.
    # Retrieval quality is EV-3's job; here we only check the graph passes the top-3 along.
    out = run(FakeLLM(Classification(category=Category.DUPLICATE_CHARGE, confidence=0.93)))

    expected = [h.chunk.id for h in index.search("Me cobraron dos veces las zapatillas", k=3)]
    assert [p["id"] for p in out["policies"]] == expected
    assert {"id", "doc", "section", "text", "score"} <= out["policies"][0].keys()
    assert [c["tool"] for c in out["tool_calls"]] == ["get_customer", "list_transactions"]
    assert all(c["ok"] for c in out["tool_calls"])
    assert out["customer"]["dni_masked"] == "*****222"
    assert [t["id"] for t in out["transactions"]] == ["T0002", "T0001"]
    assert "action" not in out and not out["errors"]


@pytest.mark.parametrize("category", [Category.SECURITY, Category.CARD_BLOCK])
def test_categories_without_movements_skip_transactions(run, category):
    core = FakeCore()
    out = run(FakeLLM(Classification(category=category, confidence=0.9)), core=core)

    assert [c["tool"] for c in out["tool_calls"]] == ["get_customer"]
    assert core.requests == ["GET /customers/C0001"]
    assert "transactions" not in out


def test_unknown_customer_requests_info(run):
    # AC-003.2: nonexistent customer -> "request data", and the lookup stops there.
    core = FakeCore()
    out = run(FakeLLM(Classification(category=Category.DUPLICATE_CHARGE, confidence=0.9)),
              customer_id="C9999", core=core)

    assert out["action"] == Action.REQUEST_INFO
    assert out["tool_calls"] == [{"tool": "get_customer", "args": {"customer_id": "C9999"},
                                  "ok": False, "error": "GET /customers/C9999: not found"}]
    assert core.requests == ["GET /customers/C9999"]
    assert out["errors"][0].startswith("lookup_customer:")


def test_core_down_escalates(run):
    # PRD 9.1.6: the tool fails, the trace shows it and the case goes to a person.
    out = run(FakeLLM(Classification(category=Category.DUPLICATE_CHARGE, confidence=0.9)),
              core=FakeCore(down=True))

    assert out["action"] == Action.ESCALATE_OPERATIONS
    assert out["tool_calls"][0]["ok"] is False
    assert "customer" not in out


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
