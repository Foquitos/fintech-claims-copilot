"""Swappable embedders.

- OpenAIEmbedder: the production one (text-embedding-3-small).
- HashEmbedder: deterministic and offline, for tests and CI. It is lexical (hashed words and
  bigrams), not semantic: good enough to check the pipeline, not to measure real quality.
"""
import hashlib
import os
import re
import unicodedata
from typing import Protocol

import numpy as np


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray: ...


def l2_normalize(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (m / norms).astype("float32")


# Spanish stopwords: policies and claims are in Spanish.
_STOPWORDS = set("""a al con de del el en es la las le lo los no o para por que se si su sus un una y
                 me mi ya hay más mas cuando como pero sin sobre este esta eso""".split())


def _tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    words = [w for w in re.findall(r"[a-z0-9]+", text) if w not in _STOPWORDS and len(w) > 2]
    # Crude stemming (first 6 chars) to merge "duplicado"/"duplicada"/"duplicados".
    stems = [w[:6] for w in words]
    return stems + [f"{a}_{b}" for a, b in zip(stems, stems[1:])]


class HashEmbedder:
    name = "hash-v1"

    def __init__(self, dim: int = 1024) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> np.ndarray:
        m = np.zeros((len(texts), self.dim), dtype="float32")
        for i, t in enumerate(texts):
            for tok in _tokens(t):
                h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
                m[i, h % self.dim] += 1.0 if "_" not in tok else 0.5
        return l2_normalize(m)


class OpenAIEmbedder:
    def __init__(self, model: str = "text-embedding-3-small", batch_size: int = 64) -> None:
        from ..llm import get_client  # lazy import: the client is only needed in production

        self.client = get_client()  # OpenAI or Azure OpenAI; the deployment is named like the model
        self.model = model
        self.name = f"openai:{model}"
        self.batch_size = batch_size
        self.dim = 1536 if model.endswith("small") else 3072

    def embed(self, texts: list[str]) -> np.ndarray:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            resp = self.client.embeddings.create(model=self.model, input=texts[i:i + self.batch_size])
            out.extend(d.embedding for d in resp.data)
        return l2_normalize(np.array(out, dtype="float32"))


def get_embedder() -> Embedder:
    has_key = os.getenv("OPENAI_API_KEY") or os.getenv("AZURE_OPENAI_ENDPOINT")
    provider = os.getenv("EMBEDDINGS_PROVIDER", "openai" if has_key else "hash")
    if provider == "openai":
        return OpenAIEmbedder(os.getenv("EMBEDDINGS_MODEL", "text-embedding-3-small"))
    return HashEmbedder()
