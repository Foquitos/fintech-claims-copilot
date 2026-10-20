"""Agent graph tests with a fake LLM and a mocked core-mock: no network, no tokens."""
import pytest
from pydantic import ValidationError

from app import prompts
from app.agent.graph import CLASSIFY_PROMPT, PROPOSE_PROMPT, build_graph
from app.agent.state import Action, Category, Classification, ProposalDraft
from app.llm import LLMOutputError, get_client

from .fakes import CLAIM, DUP, TODAY, FakeCore, FakeLLM, draft


@pytest.fixture
def run(index):
    def _run(llm, claim=CLAIM, customer_id="C0001", core=None):
        core = core or FakeCore()
        return build_graph(llm, index, core.client).invoke(
            {"claim": claim, "customer_id": customer_id})
    return _run


def test_classify_sets_category_and_prompt_version(run, cited):
    llm = FakeLLM(DUP, draft([cited]))
    out = run(llm)

    assert out["category"] == Category.DUPLICATE_CHARGE
    assert out["confidence"] == 0.93
    prompt = prompts.load(CLASSIFY_PROMPT)
    assert out["prompt_versions"]["classify"] == f"classify.v1@{prompt.sha}"
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


def test_duplicate_charge_retrieves_policy_and_checks_transactions(run, index, cited):
    # AC-003.1: C0001's trace shows the transactions tool, and both charges reach the state.
    # Retrieval quality is EV-3's job; here we only check the graph passes the top-3 along.
    out = run(FakeLLM(DUP, draft([cited])))

    expected = [h.chunk.id for h in index.search(CLAIM, k=3)]
    assert [p["id"] for p in out["policies"]] == expected
    assert {"id", "doc", "section", "text", "score"} <= out["policies"][0].keys()
    # The refund is within limits, so the write tool runs right after the reads.
    assert [c["tool"] for c in out["tool_calls"]] == [
        "get_customer", "list_transactions", "create_refund"]
    assert all(c["ok"] for c in out["tool_calls"])
    assert out["customer"]["dni_masked"] == "*****222"
    assert [t["id"] for t in out["transactions"]] == ["T0002", "T0001"]
    assert out["action"] == Action.REFUND and not out["errors"]


@pytest.mark.parametrize("category", [Category.SECURITY, Category.CARD_BLOCK])
def test_categories_without_movements_skip_transactions(run, cited, category):
    core = FakeCore()
    out = run(FakeLLM(Classification(category=category, confidence=0.9),
                      draft([cited], action=Action.ESCALATE_SECURITY)), core=core)

    assert [c["tool"] for c in out["tool_calls"]] == ["get_customer"]
    assert core.requests == ["GET /customers/C0001", "GET /health"]
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


# --- propose and validate (RF-004, RF-005, RF-011) -----------------------------------------

def test_refund_proposal_is_validated_and_kept(run, cited):
    llm = FakeLLM(DUP, draft([cited]))
    out = run(llm)

    p = out["proposal"]
    assert (p.category, p.action, p.transaction_id, p.amount) == (
        Category.DUPLICATE_CHARGE, Action.REFUND, "T0002", 45999.0)
    assert p.cited_policies == [cited]
    assert not p.needs_review and p.review_reasons == []
    assert out["propose_attempts"] == 1
    prompt = prompts.load(PROPOSE_PROMPT)
    assert out["prompt_versions"]["propose"] == f"propose.v2@{prompt.sha}"
    # The model sees the core's date, the claim, both charges and the excerpts with their ids.
    user = llm.calls[1]["user"]
    assert all(s in user for s in (TODAY, CLAIM, "T0001", "T0002", cited))


def test_three_invalid_outputs_escalate(run):
    # AC-011.1: 3 malformed replies -> escalate, with every attempt recorded.
    bad = [LLMOutputError(f"bad json {i}") for i in range(3)]
    out = run(FakeLLM(DUP, *bad))

    assert out["action"] == Action.ESCALATE_OPERATIONS
    assert out["propose_attempts"] == 3
    assert [e.split(":")[0] for e in out["errors"]] == [
        "propose attempt 1", "propose attempt 2", "propose attempt 3"]
    assert "proposal" not in out


def test_second_attempt_is_used(run, cited):
    # AC-011.2: one failure, then a valid reply that is used.
    out = run(FakeLLM(DUP, ValidationError.from_exception_data("ProposalDraft", []),
                      draft([cited])))

    assert out["action"] == Action.REFUND
    assert out["propose_attempts"] == 2
    assert len(out["errors"]) == 1


def test_missing_citation_escalates(run):
    # AC-005.1
    out = run(FakeLLM(DUP, draft([])))

    p = out["proposal"]
    assert out["action"] == p.action == Action.ESCALATE_OPERATIONS
    assert p.needs_review and "no valid policy citation" in p.review_reasons
    assert p.amount is None and p.transaction_id is None


def test_citation_not_retrieved_counts_as_missing(run):
    # AC-005.2: a made-up or non-retrieved id is dropped, leaving no valid citation.
    out = run(FakeLLM(DUP, draft(["99_inventada#0"])))

    p = out["proposal"]
    assert p.action == Action.ESCALATE_OPERATIONS
    assert p.cited_policies == []
    assert "citation not retrieved: 99_inventada#0" in p.review_reasons


def test_invalid_citation_is_dropped_when_a_valid_one_remains(run, cited):
    out = run(FakeLLM(DUP, draft([cited, "99_inventada#0"])))

    p = out["proposal"]
    assert p.action == Action.REFUND and p.cited_policies == [cited]
    assert p.needs_review  # still flagged: the model cited something it was not given


@pytest.mark.parametrize("tx, amount, reason", [
    ("T9999", 100.0, "not a looked-up transaction"),
    ("T0002", 99999.0, "exceeds transaction amount"),
])
def test_refund_must_match_a_looked_up_transaction(run, cited, tx, amount, reason):
    out = run(FakeLLM(DUP, draft([cited], transaction_id=tx, amount=amount)))

    p = out["proposal"]
    assert p.action == Action.ESCALATE_OPERATIONS
    assert any(reason in r for r in p.review_reasons)


def test_low_confidence_escalates(run, cited):
    # PRD 9.1.6: below 0.6 no write action is proposed.
    out = run(FakeLLM(DUP, draft([cited], confidence=0.4)))

    assert out["action"] == Action.ESCALATE_OPERATIONS
    assert "low confidence 0.40" in out["proposal"].review_reasons


def test_low_classification_confidence_also_counts(run, cited):
    out = run(FakeLLM(Classification(category=Category.DUPLICATE_CHARGE, confidence=0.5),
                      draft([cited])))

    assert out["action"] == Action.ESCALATE_OPERATIONS


def test_low_confidence_keeps_an_existing_escalation(run, cited):
    out = run(FakeLLM(DUP, draft([cited], action=Action.ESCALATE_FRAUD, confidence=0.4)))

    assert out["action"] == Action.ESCALATE_FRAUD
    assert not out["proposal"].needs_review


def test_refund_draft_needs_a_target():
    with pytest.raises(ValidationError):
        ProposalDraft(action=Action.REFUND, amount=None, transaction_id=None,
                      cited_policies=["x"], customer_reply="ok", confidence=0.9)


def test_non_refund_draft_drops_amount_and_target():
    d = draft(["x"], action=Action.INFORM_DEADLINE)
    assert d.amount is None and d.transaction_id is None


def test_propose_prompt_lists_every_action():
    text = prompts.load(PROPOSE_PROMPT).text
    assert all(f"- {a.value}:" in text for a in Action)


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
