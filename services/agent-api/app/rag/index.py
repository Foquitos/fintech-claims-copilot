"""FAISS vector index over the policies (inner product = cosine, vectors are normalized)."""
import json
from dataclasses import dataclass
from pathlib import Path

import faiss

from .chunking import Chunk
from .embeddings import Embedder


@dataclass
class Hit:
    chunk: Chunk
    score: float


class PolicyIndex:
    def __init__(self, embedder: Embedder, chunks: list[Chunk], index: faiss.Index) -> None:
        self.embedder = embedder
        self.chunks = chunks
        self.index = index

    @classmethod
    def build(cls, chunks: list[Chunk], embedder: Embedder) -> "PolicyIndex":
        vectors = embedder.embed([c.text for c in chunks])
        index = faiss.IndexFlatIP(vectors.shape[1])
        index.add(vectors)
        return cls(embedder, chunks, index)

    def search(self, query: str, k: int = 3, min_score: float = 0.0) -> list[Hit]:
        q = self.embedder.embed([query])
        scores, ids = self.index.search(q, min(k, len(self.chunks)))
        return [Hit(self.chunks[i], float(s)) for s, i in zip(scores[0], ids[0])
                if i != -1 and s >= min_score]

    # --- persistence ---------------------------------------------------------------------

    def save(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self.index, str(folder / "policies.faiss"))
        meta = {"embedder": self.embedder.name, "chunks": [c.as_dict() for c in self.chunks]}
        (folder / "policies.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                                              encoding="utf-8")

    @classmethod
    def load(cls, folder: Path, embedder: Embedder) -> "PolicyIndex":
        meta = json.loads((folder / "policies.json").read_text(encoding="utf-8"))
        if meta["embedder"] != embedder.name:
            # An index built with a different embeddings model returns garbage without any error.
            raise ValueError(f"Index was built with {meta['embedder']} but is being queried "
                             f"with {embedder.name}. Rebuild it with `python -m app.rag.build`.")
        chunks = [Chunk(**c) for c in meta["chunks"]]
        return cls(embedder, chunks, faiss.read_index(str(folder / "policies.faiss")))
