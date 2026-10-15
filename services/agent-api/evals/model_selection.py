"""Model selection (PA-2) and no-graph baseline (PA-1) over the dev set. Spends tokens.

    python -m evals.model_selection gpt-4.1-mini gpt-5.4-mini

For each model (an Azure deployment or OpenAI model name):
- graph: category accuracy of the agent's classify node (RF-001).
- baseline: one plain prompt, no policies or account data, asked for category and action.

The dev set is for choosing a model only; the agent golden set is written separately by hand.
Results go to evals/results/model_selection.json.
"""
import json
import sys
import time
from pathlib import Path

from pydantic import BaseModel

from app import prompts
from app.agent.graph import build_graph
from app.agent.state import Action, Category
from app.llm import OpenAIStructuredLLM

ROOT = Path(__file__).resolve().parents[1]
DEV_SET = ROOT / "evals" / "dev_set.jsonl"
BASELINE_PROMPT = "baseline.v2"
RESULTS = ROOT / "evals" / "results" / "model_selection.json"

# USD per 1M tokens (input, output), standard pay-as-you-go. Check before trusting the cost.
PRICES = {"gpt-4.1-mini": (0.40, 1.60), "gpt-5.4-mini": (0.75, 4.50)}


class BaselineAnswer(BaseModel):
    category: Category
    action: Action


def _cost_per_claim(model: str, usage: dict, n: int) -> float | None:
    if model not in PRICES:
        return None
    p_in, p_out = PRICES[model]
    return round((usage["input_tokens"] * p_in + usage["output_tokens"] * p_out) / 1e6 / n, 6)


def _run(model: str, cases: list[dict]) -> dict:
    graph_llm, base_llm = OpenAIStructuredLLM(model), OpenAIStructuredLLM(model)
    graph = build_graph(graph_llm)
    baseline = prompts.load(BASELINE_PROMPT)
    rows, graph_s, base_s = [], 0.0, 0.0
    for c in cases:
        t = time.perf_counter()
        out = graph.invoke({"claim": c["claim"], "customer_id": c["customer_id"]})
        graph_s += time.perf_counter() - t
        t = time.perf_counter()
        b = base_llm.generate(baseline.text, f"Cliente {c['customer_id']}: {c['claim']}", BaselineAnswer)
        base_s += time.perf_counter() - t
        rows.append({"id": c["id"], "expected": [c["category"], c["action"]],
                     "graph_category": out["category"].value,
                     "baseline": [b.category.value, b.action.value]})
    n = len(cases)
    return {
        "model": model,
        "baseline_prompt": f"{baseline.id}@{baseline.sha}",
        "graph_category_acc": sum(r["graph_category"] == r["expected"][0] for r in rows) / n,
        "baseline_category_acc": sum(r["baseline"][0] == r["expected"][0] for r in rows) / n,
        "baseline_action_acc": sum(r["baseline"][1] == r["expected"][1] for r in rows) / n,
        "graph_avg_latency_s": round(graph_s / n, 2),
        "baseline_avg_latency_s": round(base_s / n, 2),
        "graph_cost_per_claim_usd": _cost_per_claim(model, graph_llm.usage, n),
        "baseline_cost_per_claim_usd": _cost_per_claim(model, base_llm.usage, n),
        "graph_usage": graph_llm.usage,
        "baseline_usage": base_llm.usage,
        "misses": [r for r in rows if r["graph_category"] != r["expected"][0]
                   or r["baseline"] != r["expected"]],
    }


if __name__ == "__main__":
    cases = [json.loads(line) for line in DEV_SET.read_text(encoding="utf-8").splitlines() if line]
    results = [_run(m, cases) for m in sys.argv[1:] or ["gpt-4.1-mini"]]
    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
