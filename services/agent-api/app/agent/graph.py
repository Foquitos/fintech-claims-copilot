"""Claims agent graph.

    START -> classify -> (other) -> out_of_scope -> END
                      -> (else)  -> retrieve_policies -> lookup_customer
    lookup_customer -> (failed)       -> END
                    -> (else)         -> propose
    propose         -> (invalid x3)   -> END
                    -> (else)         -> validate
    validate        -> (needs a supervisor) -> approval -> (approved refund) -> execute -> END
                                                        -> (else)           -> END
                    -> (refund)       -> execute -> END
                    -> (else)         -> END

`approval` pauses the run with interrupt() (RF-006); with a checkpointer the case waits in
the database until a supervisor resumes it with their decision (RF-007).

The LLM, the policy index and the core-mock client are injected, so tests build the same
graph with fakes and spend no tokens.
"""
import json
from datetime import date
from decimal import Decimal

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import ValidationError

from .. import prompts
from ..llm import LLMOutputError, StructuredLLM
from ..rag.index import PolicyIndex
from ..tools.core import CoreClient, CoreError, NotFound
from .state import (Action, Category, ClaimState, Classification, Decision, Proposal,
                    ProposalDraft)

CLASSIFY_PROMPT = "classify.v1"
PROPOSE_PROMPT = "propose.v2"
PROPOSE_ATTEMPTS = 3  # RF-011: the first try plus 2 retries
MIN_CONFIDENCE = 0.6  # PRD 9.1.6: below this, escalate instead of acting
POLICIES_K = 3  # EV-3 measures hit@3, so the proposal sees the same 3 chunks the eval scores

# When to ask a supervisor (RF-006). core-mock enforces the same limits on its side, so a
# mistake here can delay a refund but never approve one.
APPROVAL_LIMIT = Decimal("50000")
NEW_ACCOUNT_APPROVAL_LIMIT = Decimal("20000")
NEW_ACCOUNT_DAYS = 90

# Checkpoints may only rebuild these classes (LangGraph warns on, and will block, any other).
# Note: PostgresSaver stores str values inline as JSON, so str-based enums such as Category
# and Action come back as plain strings after a resume; nodes normalize them where it matters.
SERDE = JsonPlusSerializer(allowed_msgpack_modules=[
    ("app.agent.state", name) for name in ("Action", "Category", "Proposal", "Decision")])

# Categories whose resolution depends on the customer's movements. Card block and security
# claims are decided from the customer record and the policy alone.
NEEDS_TRANSACTIONS = {Category.DUPLICATE_CHARGE, Category.TRANSFER_NOT_CREDITED,
                      Category.UNRECOGNIZED_PURCHASE, Category.QR_PAYMENT, Category.CASHBACK}


def build_graph(llm: StructuredLLM, index: PolicyIndex, core: CoreClient, checkpointer=None):
    def classify(state: ClaimState) -> dict:
        prompt = prompts.load(CLASSIFY_PROMPT)
        versions = {"classify": f"{prompt.id}@{prompt.sha}"}
        try:
            result = llm.generate(prompt.text, state["claim"], Classification)
        except (LLMOutputError, ValidationError) as e:
            # Unusable output is not a crash: route it like an unknown claim, which takes no
            # tools and goes to a human. Timeouts and connection errors still propagate (503).
            return {"category": Category.OTHER, "confidence": 0.0,
                    "prompt_versions": versions, "errors": [f"classify: {e}"]}
        return {"category": result.category, "confidence": result.confidence,
                "prompt_versions": versions}

    def out_of_scope(_: ClaimState) -> dict:
        # CA-001.2: no tool runs for claims outside the wallet's scope; a person takes it.
        return {"action": Action.ESCALATE_OPERATIONS}

    def retrieve_policies(state: ClaimState) -> dict:
        # RF-002: the claim text is the query, as in the retrieval golden set (EV-3).
        hits = index.search(state["claim"], k=POLICIES_K)
        return {"policies": [{"id": h.chunk.id, "doc": h.chunk.doc, "section": h.chunk.section,
                              "text": h.chunk.text, "score": round(h.score, 4)} for h in hits]}

    def lookup_customer(state: ClaimState) -> dict:
        # RF-003: read-only tools. Every call is recorded, including the failed ones.
        customer_id = state["customer_id"]
        calls: list[dict] = []
        out: dict = {}
        try:
            out["customer"] = _call(calls, "get_customer", core.get_customer,
                                    customer_id=customer_id)
            if Category(state["category"]) in NEEDS_TRANSACTIONS:
                out["transactions"] = _call(calls, "list_transactions", core.list_transactions,
                                            customer_id=customer_id)
            out["today"] = core.today()
        except NotFound as e:
            # AC-003.2: unknown customer -> ask for the data; no write tool is ever reached.
            return {**out, "tool_calls": calls, "action": Action.REQUEST_INFO,
                    "errors": [f"lookup_customer: {e}"]}
        except CoreError as e:
            # PRD 9.1.6: core-mock down or slow -> escalate instead of guessing.
            return {**out, "tool_calls": calls, "action": Action.ESCALATE_OPERATIONS,
                    "errors": [f"lookup_customer: {e}"]}
        return {**out, "tool_calls": calls}

    def propose(state: ClaimState) -> dict:
        prompt = prompts.load(PROPOSE_PROMPT)
        versions = {"propose": f"{prompt.id}@{prompt.sha}"}
        user = _proposal_input(state)
        errors = []
        for attempt in range(1, PROPOSE_ATTEMPTS + 1):
            try:
                draft = llm.generate(prompt.text, user, ProposalDraft)
            except (LLMOutputError, ValidationError) as e:
                # RF-011: retry malformed output. Provider errors propagate, as in classify.
                errors.append(f"propose attempt {attempt}: {e}")
                continue
            return {"proposal": Proposal(**draft.model_dump(), category=Category(state["category"])),
                    "propose_attempts": attempt, "prompt_versions": versions, "errors": errors}
        return {"action": Action.ESCALATE_OPERATIONS, "propose_attempts": PROPOSE_ATTEMPTS,
                "prompt_versions": versions, "errors": errors}

    def validate(state: ClaimState) -> dict:
        # Checks the model cannot talk its way past. Each failure escalates and flags review.
        p = state["proposal"]
        fields = p.model_dump()
        reasons = []

        retrieved = {pol["id"] for pol in state.get("policies", [])}
        fields["cited_policies"] = [c for c in p.cited_policies if c in retrieved]
        # AC-005.2: a citation outside the retrieved excerpts counts as no citation.
        reasons += [f"citation not retrieved: {c}" for c in p.cited_policies if c not in retrieved]
        if not fields["cited_policies"]:
            reasons.append("no valid policy citation")  # AC-005.1
            fields["action"] = Action.ESCALATE_OPERATIONS

        if fields["action"] == Action.REFUND:
            tx = next((t for t in state.get("transactions", []) if t["id"] == p.transaction_id), None)
            if tx is None:
                reasons.append(f"refund target {p.transaction_id} is not a looked-up transaction")
                fields["action"] = Action.ESCALATE_OPERATIONS
            elif p.amount > float(tx["amount"]):
                reasons.append(f"refund {p.amount} exceeds transaction amount {tx['amount']}")
                fields["action"] = Action.ESCALATE_OPERATIONS

        confidence = min(p.confidence, state.get("confidence", 1.0))
        if confidence < MIN_CONFIDENCE and not fields["action"].value.startswith("escalate"):
            reasons.append(f"low confidence {confidence:.2f}")
            fields["action"] = Action.ESCALATE_OPERATIONS

        if fields["action"] != p.action:
            reasons.append(f"action changed from {p.action.value} to {fields['action'].value}")
        fields.update(needs_review=bool(reasons), review_reasons=reasons)
        final = Proposal(**fields)  # re-validates: clears amount and target if no longer a refund
        return {"proposal": final, "action": final.action,
                "approval_reasons": _approval_reasons(final, state)}

    def approval(state: ClaimState) -> dict:
        # RF-006: the run stops here and the checkpointer keeps the case. Whatever the
        # supervisor sends back through Command(resume=...) is the value of interrupt().
        answer = interrupt({"proposal": state["proposal"].model_dump(mode="json"),
                            "reasons": state["approval_reasons"]})
        return {"decision": Decision.model_validate(answer)}

    def execute(state: ClaimState) -> dict:
        # The only write tool. Runs for refunds within limits, or after a supervisor approved.
        p = state["proposal"]
        calls: list[dict] = []
        try:
            refund = _call(calls, "create_refund", core.create_refund,
                           transaction_id=p.transaction_id, amount=p.amount,
                           reason=f"{p.category.value} claim, copilot proposal")
            decision = state.get("decision")
            if refund["status"] == "pending_approval" and decision and decision.approved:
                refund = _call(calls, "approve_refund", core.approve_refund,
                               refund_id=refund["id"])
        except CoreError as e:
            # No automatic retry of writes (PRD 9.1.6): a person checks what happened.
            return {"tool_calls": calls, "action": Action.ESCALATE_OPERATIONS,
                    "errors": [f"execute: {e}"]}

        update: dict = {"customer_reply": f"{p.customer_reply} Número de solicitud: {refund['id']}."}
        if refund["status"] != "approved":
            # core-mock held a refund the graph thought was within limits: its rule wins.
            update.update(needs_review=True, review_reasons=[
                *p.review_reasons, f"core-mock left refund {refund['id']} {refund['status']}"])
        return {"refund": refund, "proposal": p.model_copy(update=update), "tool_calls": calls}

    def route_after_classify(state: ClaimState) -> str:
        return "out_of_scope" if state["category"] == Category.OTHER else "retrieve_policies"

    def route_if_decided(next_node: str):
        # A node that already set the final action (lookup failed, output invalid x3) ends the run.
        return lambda state: END if "action" in state else next_node

    def route_after_validate(state: ClaimState) -> str:
        if state["approval_reasons"]:
            return "approval"
        return "execute" if state["action"] == Action.REFUND else END

    def route_after_approval(state: ClaimState) -> str:
        approved = state["decision"].approved
        return "execute" if approved and state["action"] == Action.REFUND else END

    g = StateGraph(ClaimState)
    g.add_node("classify", classify)
    g.add_node("out_of_scope", out_of_scope)
    g.add_node("retrieve_policies", retrieve_policies)
    g.add_node("lookup_customer", lookup_customer)
    g.add_node("propose", propose)
    g.add_node("validate", validate)
    g.add_node("approval", approval)
    g.add_node("execute", execute)
    g.add_edge(START, "classify")
    g.add_conditional_edges("classify", route_after_classify,
                            ["out_of_scope", "retrieve_policies"])
    g.add_edge("out_of_scope", END)
    g.add_edge("retrieve_policies", "lookup_customer")
    g.add_conditional_edges("lookup_customer", route_if_decided("propose"), ["propose", END])
    g.add_conditional_edges("propose", route_if_decided("validate"), ["validate", END])
    g.add_conditional_edges("validate", route_after_validate, ["approval", "execute", END])
    g.add_conditional_edges("approval", route_after_approval, ["execute", END])
    g.add_edge("execute", END)
    return g.compile(checkpointer=checkpointer)


def _call(calls: list[dict], tool: str, fn, **args):
    """Run a core-mock tool and record the call, failed or not, for the trace."""
    try:
        result = fn(**args)
    except CoreError as e:
        calls.append({"tool": tool, "args": args, "ok": False, "error": str(e)})
        raise
    calls.append({"tool": tool, "args": args, "ok": True})
    return result


def _approval_reasons(p: Proposal, state: ClaimState) -> list[str]:
    """RF-006: large refunds, refunds on new accounts and possible fraud wait for a human."""
    if p.action == Action.ESCALATE_FRAUD:
        return ["possible fraud"]
    if p.action != Action.REFUND:
        return []
    amount = Decimal(str(p.amount))
    if amount > APPROVAL_LIMIT:
        return [f"refund above ${APPROVAL_LIMIT:,}"]
    created = date.fromisoformat(state["customer"]["created_at"][:10])
    age_days = (date.fromisoformat(state["today"]) - created).days
    if age_days < NEW_ACCOUNT_DAYS and amount > NEW_ACCOUNT_APPROVAL_LIMIT:
        return [f"refund above ${NEW_ACCOUNT_APPROVAL_LIMIT:,} on a {age_days}-day-old account"]
    return []


def _proposal_input(state: ClaimState) -> str:
    """The user message for the proposal step: the claim plus everything the graph gathered."""
    policies = [{"id": p["id"], "text": p["text"]} for p in state.get("policies", [])]
    return json.dumps({
        "today": state.get("today"),
        "claim": state["claim"],
        "category": Category(state["category"]).value,
        "customer": state.get("customer"),
        "transactions": state.get("transactions", []),
        "policies": policies,
    }, ensure_ascii=False, indent=1)
