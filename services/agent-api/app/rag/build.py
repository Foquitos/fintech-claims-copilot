"""Build the policy index: `python -m app.rag.build`."""
from pathlib import Path

from .chunking import load_policies
from .embeddings import get_embedder
from .index import PolicyIndex

ROOT = Path(__file__).resolve().parents[2]
POLICIES_DIR = ROOT / "policies"
INDEX_DIR = ROOT / "data" / "index"


def build(policies_dir: Path | None = None, index_dir: Path | None = None) -> PolicyIndex:
    chunks = load_policies(policies_dir or POLICIES_DIR)
    idx = PolicyIndex.build(chunks, get_embedder())
    idx.save(index_dir or INDEX_DIR)
    return idx


if __name__ == "__main__":
    idx = build()
    print(f"Index ready: {len(idx.chunks)} chunks with {idx.embedder.name} → {INDEX_DIR}")
