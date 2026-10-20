"""Human approval (RF-006/007) and the refund write, with an in-memory checkpointer."""
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.agent.graph import SERDE, build_graph
from app.agent.state import Action, Classification, Category

from .fakes import DUP, FakeCore, FakeLLM, draft

C0006_CLAIM = "Me cobraron dos veces la heladera de Electrodomésticos Norte"


@pytest.fixture
def case(index):
    """Runs a claim as a case (one checkpointer thread) and lets the test resume it."""
    class Case:
        def __init__(self, llm, customer_id="C0001", core=None, claim="Me cobraron dos veces"):
            self.core = core or FakeCore()
            self.graph = build_graph(llm, index, self.core.client, InMemorySaver(serde=SERDE))
            self.config = {"configurable": {"thread_id": "case-1"}}
            self.graph.invoke({"claim": claim, "customer_id": customer_id}, self.config)

        @property
        def snapshot(self):
            return self.graph.get_state(self.config)

        @property
        def values(self):
            return self.snapshot.values

        def decide(self, approved: bool):
            self.graph.invoke(Command(resume={"approved": approved, "supervisor": "Paula"}),
                              self.config)
    return Case


def test_refund_within_limits_runs_without_pause(case, cited):
    # AC-006.2 shape: an old account and an amount under $50,000 -> no pause, refund approved.
    c = case(FakeLLM(DUP, draft([cited], amount=18500.0)))

    assert not c.snapshot.interrupts
    assert c.values["approval_reasons"] == []
    assert c.values["refund"]["status"] == "approved"
    assert c.core.writes == ["POST /refunds"]
    assert c.values["proposal"].customer_reply.endswith("Número de solicitud: R1.")


def test_large_refund_pauses_without_writing(case, cited):
    # AC-006.1: C0006's $480,000 refund waits for a supervisor and nothing is written.
    c = case(FakeLLM(DUP, draft([cited], amount=480000.0, transaction_id="T0008")),
             customer_id="C0006", claim=C0006_CLAIM)

    assert c.snapshot.interrupts
    assert c.snapshot.interrupts[0].value["reasons"] == ["refund above $50,000"]
    assert c.core.writes == []
    assert "refund" not in c.values


def test_approval_resumes_and_writes(case, cited):
    # AC-007.1: approved -> the refund is created, approved in core-mock, and the reply
    # carries the request number.
    c = case(FakeLLM(DUP, draft([cited], amount=480000.0, transaction_id="T0008")),
             customer_id="C0006", claim=C0006_CLAIM)
    c.decide(approved=True)

    assert not c.snapshot.interrupts
    assert c.values["decision"].approved and c.values["decision"].supervisor == "Paula"
    assert c.core.writes == ["POST /refunds", "POST /refunds/R1/approve"]
    assert c.values["refund"]["status"] == "approved"
    assert "Número de solicitud: R1" in c.values["proposal"].customer_reply


def test_rejection_writes_nothing(case, cited):
    c = case(FakeLLM(DUP, draft([cited], amount=480000.0, transaction_id="T0008")),
             customer_id="C0006", claim=C0006_CLAIM)
    c.decide(approved=False)

    assert not c.snapshot.interrupts
    assert c.values["decision"].approved is False
    assert c.core.writes == []


def test_new_account_has_a_lower_limit(case, cited):
    c = case(FakeLLM(DUP, draft([cited], amount=25000.0, transaction_id="T0008")),
             customer_id="C0006", claim=C0006_CLAIM)

    assert c.snapshot.interrupts
    assert c.values["approval_reasons"] == ["refund above $20,000 on a 60-day-old account"]


def test_fraud_escalation_pauses_and_approval_writes_nothing(case, cited):
    llm = FakeLLM(Classification(category=Category.UNRECOGNIZED_PURCHASE, confidence=0.9),
                  draft([cited], action=Action.ESCALATE_FRAUD))
    c = case(llm)
    assert c.values["approval_reasons"] == ["possible fraud"]

    c.decide(approved=True)
    assert c.values["action"] == Action.ESCALATE_FRAUD
    assert c.core.writes == []


def test_core_holding_a_refund_wins_and_flags_review(case, cited):
    # The graph thought the refund was within limits; core-mock disagreed. Its rule wins.
    c = case(FakeLLM(DUP, draft([cited], amount=18500.0)), core=FakeCore(hold_refunds=True))

    p = c.values["proposal"]
    assert c.values["refund"]["status"] == "pending_approval"
    assert p.needs_review and "core-mock left refund R1 pending_approval" in p.review_reasons


def test_failed_write_escalates_without_retry(case, cited):
    c = case(FakeLLM(DUP, draft([cited], amount=18500.0)), core=FakeCore(writes_down=True))

    assert c.values["action"] == Action.ESCALATE_OPERATIONS
    assert c.core.writes == ["POST /refunds"]  # one attempt, no retry
    assert c.values["errors"][-1].startswith("execute:")


def test_resume_works_with_enums_stored_as_plain_strings(index, cited):
    # PostgresSaver returns str-based enums as plain strings. Simulate a resumed state.
    from app.agent.graph import _proposal_input
    text = _proposal_input({"claim": "x", "category": "duplicate_charge", "today": "2026-10-10"})
    assert '"category": "duplicate_charge"' in text
