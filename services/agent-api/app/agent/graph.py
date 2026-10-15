"""Claims agent graph.

Phase 2, week 1:  START -> classify -> (other) -> out_of_scope -> END
                                    -> (else)  -> END   # RF-002/003 nodes plug in here

The LLM is injected, so tests build the same graph with a fake model.
"""
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from .. import prompts
from ..llm import LLMOutputError, StructuredLLM
from .state import Action, Category, ClaimState, Classification

CLASSIFY_PROMPT = "classify.v1"


def build_graph(llm: StructuredLLM):
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

    def route_after_classify(state: ClaimState) -> str:
        return "out_of_scope" if state["category"] == Category.OTHER else END

    g = StateGraph(ClaimState)
    g.add_node("classify", classify)
    g.add_node("out_of_scope", out_of_scope)
    g.add_edge(START, "classify")
    g.add_conditional_edges("classify", route_after_classify, ["out_of_scope", END])
    g.add_edge("out_of_scope", END)
    return g.compile()
