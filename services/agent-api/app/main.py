"""agent-api. Phase 1: policy search (RAG). Phase 2 adds the LangGraph agent."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel, Field

from .rag import build as rag_build
from .rag.embeddings import get_embedder
from .rag.index import PolicyIndex

state: dict = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    embedder = get_embedder()
    try:
        state["index"] = PolicyIndex.load(rag_build.INDEX_DIR, embedder)
    except (FileNotFoundError, ValueError):
        state["index"] = rag_build.build()  # first run, or the embeddings model changed
    yield


app = FastAPI(title="agent-api · Claims copilot", version="0.1.0", lifespan=lifespan)


class SearchIn(BaseModel):
    query: str = Field(min_length=3, max_length=500)
    k: int = Field(default=3, ge=1, le=10)


class PolicyHit(BaseModel):
    doc: str
    title: str
    section: str
    text: str
    score: float


@app.get("/health")
def health() -> dict:
    idx: PolicyIndex = state["index"]
    return {"status": "ok", "embedder": idx.embedder.name, "chunks": len(idx.chunks)}


@app.post("/policies/search", response_model=list[PolicyHit])
def search_policies(body: SearchIn):
    hits = state["index"].search(body.query, k=body.k)
    return [PolicyHit(doc=h.chunk.doc, title=h.chunk.title, section=h.chunk.section,
                      text=h.chunk.text, score=round(h.score, 4)) for h in hits]
