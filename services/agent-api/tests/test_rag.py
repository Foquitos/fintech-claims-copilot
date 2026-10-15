from pathlib import Path

from fastapi.testclient import TestClient

from app.rag.chunking import chunk_markdown, load_policies
from app.rag.embeddings import HashEmbedder
from app.rag.index import PolicyIndex
from evals.retrieval import evaluate

ROOT = Path(__file__).resolve().parents[1]


def test_chunks_repeat_document_title():
    chunks = chunk_markdown("doc", "# Title\n\n## Deadlines\nTwo days.\n\n## Exceptions\nNone.")
    assert [c.section for c in chunks] == ["Deadlines", "Exceptions"]
    assert all(c.text.startswith("Title — ") for c in chunks)


def test_all_policies_are_chunked():
    chunks = load_policies(ROOT / "policies")
    assert len({c.doc for c in chunks}) == 8
    assert len({c.id for c in chunks}) == len(chunks)


def test_index_roundtrip(tmp_path):
    emb = HashEmbedder()
    idx = PolicyIndex.build(load_policies(ROOT / "policies"), emb)
    idx.save(tmp_path)
    again = PolicyIndex.load(tmp_path, emb)
    q = "cobro duplicado"
    assert [h.chunk.id for h in idx.search(q)] == [h.chunk.id for h in again.search(q)]


def test_index_rejects_other_embedder(tmp_path):
    idx = PolicyIndex.build(load_policies(ROOT / "policies"), HashEmbedder())
    idx.save(tmp_path)
    other = HashEmbedder()
    other.name = "other-model"
    try:
        PolicyIndex.load(tmp_path, other)
    except ValueError:
        return
    raise AssertionError("should reject an index built with another embedder")


def test_retrieval_golden_set_offline():
    # Regression floor with the lexical embedder. Expect much higher with OpenAI.
    result = evaluate(HashEmbedder())
    assert result["hit@3"] >= 0.9, result["misses"]


def test_search_endpoint(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "hash")
    monkeypatch.setattr("app.rag.build.INDEX_DIR", tmp_path)
    from app.main import app
    with TestClient(app) as c:
        assert c.get("/health").json()["embedder"] == "hash-v1"
        hits = c.post("/policies/search", json={"query": "pago QR rechazado", "k": 2}).json()
        assert hits[0]["doc"] == "05_pagos_qr"
