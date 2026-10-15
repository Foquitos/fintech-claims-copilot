"""Retrieval evaluation: hit@1, hit@3 and MRR over the golden set.

    python -m evals.retrieval                      # with the configured embedder
    EMBEDDINGS_PROVIDER=hash python -m evals.retrieval
"""
import json
from pathlib import Path

from app.rag.chunking import load_policies
from app.rag.embeddings import Embedder, get_embedder
from app.rag.index import PolicyIndex

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "evals" / "retrieval_golden.jsonl"


def evaluate(embedder: Embedder, k: int = 3) -> dict:
    index = PolicyIndex.build(load_policies(ROOT / "policies"), embedder)
    cases = [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line]
    hit1 = hitk = rr = 0.0
    misses = []
    for case in cases:
        docs = [h.chunk.doc for h in index.search(case["query"], k=10)]
        # A document counts once even if several of its sections show up.
        ranking = list(dict.fromkeys(docs))
        pos = ranking.index(case["expected_doc"]) + 1 if case["expected_doc"] in ranking else None
        hit1 += pos == 1
        hitk += pos is not None and pos <= k
        rr += 1 / pos if pos else 0
        if pos != 1:
            misses.append({"query": case["query"], "expected": case["expected_doc"],
                           "got": ranking[:3]})
    n = len(cases)
    return {"embedder": embedder.name, "n": n, "hit@1": hit1 / n, f"hit@{k}": hitk / n,
            "mrr": rr / n, "misses": misses}


if __name__ == "__main__":
    print(json.dumps(evaluate(get_embedder()), ensure_ascii=False, indent=2))
