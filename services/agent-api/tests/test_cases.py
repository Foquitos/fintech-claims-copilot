"""Case endpoints (RF-006/007) with a fake LLM, a mocked core-mock and an in-memory checkpointer."""
import httpx
import openai
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.graph import SERDE, build_graph

from .fakes import DUP, FakeCore, FakeLLM, draft


@pytest.fixture
def api(monkeypatch, tmp_path, index):
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "hash")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")  # the real client is built, never called
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("CHECKPOINT_DB_URL", raising=False)
    monkeypatch.setattr("app.rag.build.INDEX_DIR", tmp_path)
    from app.main import app, state

    def use(llm, core=None):
        core = core or FakeCore()
        state["graph"] = build_graph(llm, index, core.client, InMemorySaver(serde=SERDE))
        return core

    with TestClient(app) as client:
        client.use = use
        yield client


def test_refund_case_is_proposed_and_executed(api, cited):
    api.use(FakeLLM(DUP, draft([cited], amount=18500.0)))
    r = api.post("/cases", json={"claim": "Me cobraron dos veces", "customer_id": "C0001"})

    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "proposed" and body["action"] == "refund"
    assert body["refund"]["status"] == "approved"
    assert api.get(f"/cases/{body['id']}").json() == body


def test_pending_case_can_be_approved(api, cited):
    core = api.use(FakeLLM(DUP, draft([cited], amount=480000.0, transaction_id="T0008")))
    case = api.post("/cases", json={"claim": "Me cobraron dos veces la heladera",
                                    "customer_id": "C0006"}).json()
    assert case["status"] == "pending_approval"
    assert case["approval_reasons"] == ["refund above $50,000"]
    assert case["refund"] is None and core.writes == []

    done = api.post(f"/cases/{case['id']}/decision",
                    json={"approved": True, "supervisor": "Paula", "note": "Verificado"}).json()
    assert done["status"] == "approved"
    assert done["decision"] == {"approved": True, "supervisor": "Paula", "note": "Verificado"}
    assert done["refund"]["status"] == "approved"


def test_decision_on_a_case_that_is_not_pending_is_409(api, cited):
    api.use(FakeLLM(DUP, draft([cited], amount=18500.0)))
    case = api.post("/cases", json={"claim": "Me cobraron dos veces", "customer_id": "C0001"}).json()

    r = api.post(f"/cases/{case['id']}/decision", json={"approved": True, "supervisor": "Paula"})
    assert r.status_code == 409


def test_unknown_case_is_404(api):
    api.use(FakeLLM())
    assert api.get("/cases/K404").status_code == 404


def test_claim_too_long_is_rejected(api):
    api.use(FakeLLM())  # any LLM call would fail the test
    r = api.post("/cases", json={"claim": "x" * 2001, "customer_id": "C0001"})
    assert r.status_code == 422


def test_provider_down_is_503(api):
    request = httpx.Request("POST", "http://llm")
    api.use(FakeLLM(openai.APITimeoutError(request=request)))
    r = api.post("/cases", json={"claim": "Me cobraron dos veces", "customer_id": "C0001"})
    assert r.status_code == 503


def test_without_credentials_cases_are_503(api):
    from app.main import state
    state["graph"] = None
    r = api.post("/cases", json={"claim": "Me cobraron dos veces", "customer_id": "C0001"})
    assert r.status_code == 503
