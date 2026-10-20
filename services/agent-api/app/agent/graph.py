"""Claims agent graph.

Phase 2, week 2 (so far):

    START -> classify -> (other) -> out_of_scope -> END
                      -> (else)  -> retrieve_policies -> lookup_customer
    lookup_customer -> (failed)  -> END
                    -> (else)    -> propose -> (invalid x3) -> END
                                            -> (else)       -> validate -> END
                                                               # RF-006 approval plugs in here

The LLM, the policy index and the core-mock client are injected, so tests build the same
graph with fakes and spend no tokens.
"""
import json

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from .. import prompts
from ..llm import LLMOutputError, StructuredLLM
from ..rag.index import PolicyIndex
from ..tools.core import CoreClient, CoreError, NotFound
from .state import Action, Category, ClaimState, Classification, Proposal, ProposalDraft

CLASSIFY_PROMPT = "classify.v1"
PROPOSE_PROMPT = "propose.v1"
PROPOSE_ATTEMPTS = 3  # RF-011: the first try plus 2 retries
MIN_CONFIDENCE = 0.6  # PRD 9.1.6: below this, escalate instead of acting
POLICIES_K = 3  # EV-3 measures hit@3, so the proposal sees the same 3 chunks the eval scores

# Categories whose resolution depends on the customer's movements. Card block and security
# claims are decided from the customer record and the policy alone.
NEEDS_TRANSACTIONS = {Category.DUPLICATE_CHARGE, Category.TRANSFER_NOT_CREDITED,
                      Category.UNRECOGNIZED_PURCHASE, Category.QR_PAYMENT, Category.CASHBACK}


def build_graph(llm: StructuredLLM, index: PolicyIndex, core: CoreClient):
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

        def call(tool: str, fn, **args):
            try:
                result = fn(**args)
            except CoreError as e:
                calls.append({"tool": tool, "args": args, "ok": False, "error": str(e)})
                raise
            calls.append({"tool": tool, "args": args, "ok": True})
            return result

        out: dict = {}
        try:
            out["customer"] = call("get_customer", core.get_customer, customer_id=customer_id)
            if state["category"] in NEEDS_TRANSACTIONS:
                out["transactions"] = call("list_transactions", core.list_transactions,
                                           customer_id=customer_id)
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
            return {"proposal": Proposal(**draft.model_dump(), category=state["category"]),
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
        return {"proposal": final, "action": final.action}

    def route_after_classify(state: ClaimState) -> str:
        return "out_of_scope" if state["category"] == Category.OTHER else "retrieve_policies"

    def route_if_decided(next_node: str):
        # A node that already set the final action (lookup failed, output invalid x3) ends the run.
        return lambda state: END if "action" in state else next_node

    g = StateGraph(ClaimState)
    g.add_node("classify", classify)
    g.add_node("out_of_scope", out_of_scope)
    g.add_node("retrieve_policies", retrieve_policies)
    g.add_node("lookup_customer", lookup_customer)
    g.add_node("propose", propose)
    g.add_node("validate", validate)
    g.add_edge(START, "classify")
    g.add_conditional_edges("classify", route_after_classify,
                            ["out_of_scope", "retrieve_policies"])
    g.add_edge("out_of_scope", END)
    g.add_edge("retrieve_policies", "lookup_customer")
    g.add_conditional_edges("lookup_customer", route_if_decided("propose"), ["propose", END])
    g.add_conditional_edges("propose", route_if_decided("validate"), ["validate", END])
    g.add_edge("validate", END)
    return g.compile()


def _proposal_input(state: ClaimState) -> str:
    """The user message for the proposal step: the claim plus everything the graph gathered."""
    policies = [{"id": p["id"], "text": p["text"]} for p in state.get("policies", [])]
    return json.dumps({
        "claim": state["claim"],
        "category": state["category"].value,
        "customer": state.get("customer"),
        "transactions": state.get("transactions", []),
        "policies": policies,
    }, ensure_ascii=False, indent=1)
