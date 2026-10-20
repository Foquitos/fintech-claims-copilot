"""Claims agent graph.

Phase 2, week 2 (so far):

    START -> classify -> (other) -> out_of_scope -> END
                      -> (else)  -> retrieve_policies -> lookup_customer -> END
                                                         # RF-004 propose node plugs in here

The LLM, the policy index and the core-mock client are injected, so tests build the same
graph with fakes and spend no tokens.
"""
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from .. import prompts
from ..llm import LLMOutputError, StructuredLLM
from ..rag.index import PolicyIndex
from ..tools.core import CoreClient, CoreError, NotFound
from .state import Action, Category, ClaimState, Classification

CLASSIFY_PROMPT = "classify.v1"
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

    def route_after_classify(state: ClaimState) -> str:
        return "out_of_scope" if state["category"] == Category.OTHER else "retrieve_policies"

    g = StateGraph(ClaimState)
    g.add_node("classify", classify)
    g.add_node("out_of_scope", out_of_scope)
    g.add_node("retrieve_policies", retrieve_policies)
    g.add_node("lookup_customer", lookup_customer)
    g.add_edge(START, "classify")
    g.add_conditional_edges("classify", route_after_classify,
                            ["out_of_scope", "retrieve_policies"])
    g.add_edge("out_of_scope", END)
    g.add_edge("retrieve_policies", "lookup_customer")
    g.add_edge("lookup_customer", END)
    return g.compile()
