"""agent-api: policy search (RAG) and claim cases run by the LangGraph agent."""
import logging
import os
import uuid
from contextlib import asynccontextmanager

import openai
from fastapi import FastAPI, HTTPException
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel, Field

from .agent.graph import SERDE, build_graph
from .agent.state import Action, Category, Decision
from .llm import OpenAIStructuredLLM
from .rag import build as rag_build
from .rag.embeddings import get_embedder
from .rag.index import PolicyIndex
from .tools.core import CoreClient

log = logging.getLogger(__name__)
state: dict = {}

# Provider down or slow: a clear 503 for the support agent, never a made-up answer (PRD 9.1.6).
PROVIDER_ERRORS = (openai.APIConnectionError, openai.InternalServerError, TimeoutError)


@asynccontextmanager
async def lifespan(_: FastAPI):
    embedder = get_embedder()
    try:
        state["index"] = PolicyIndex.load(rag_build.INDEX_DIR, embedder)
    except (FileNotFoundError, ValueError):
        state["index"] = rag_build.build()  # first run, or the embeddings model changed
    pool = None
    db_url = os.getenv("CHECKPOINT_DB_URL")
    if db_url:
        # Cases survive restarts: each one is a thread whose state lives in Postgres.
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        pool = ConnectionPool(db_url, open=True, kwargs={
            "autocommit": True, "prepare_threshold": 0, "row_factory": dict_row})
        checkpointer = PostgresSaver(pool, serde=SERDE)
        checkpointer.setup()
    else:
        log.warning("CHECKPOINT_DB_URL is not set: cases live in memory and die with the process")
        checkpointer = InMemorySaver(serde=SERDE)
    try:
        llm = OpenAIStructuredLLM()
    except openai.OpenAIError:
        # No credentials: policy search still works offline; /cases answers 503.
        log.warning("No LLM credentials: the agent is disabled")
        state["graph"] = None
    else:
        state["graph"] = build_graph(llm, state["index"], CoreClient(), checkpointer)
    yield
    if pool:
        pool.close()


app = FastAPI(title="agent-api · Claims copilot", version="0.2.0", lifespan=lifespan)


class SearchIn(BaseModel):
    query: str = Field(min_length=3, max_length=500)
    k: int = Field(default=3, ge=1, le=10)


class PolicyHit(BaseModel):
    doc: str
    title: str
    section: str
    text: str
    score: float


class CaseIn(BaseModel):
    claim: str = Field(min_length=3, max_length=2000)  # PRD 9.1.6: longer claims are rejected
    customer_id: str = Field(min_length=1, max_length=12)


class CaseOut(BaseModel):
    id: str
    status: str  # proposed | pending_approval | approved | rejected
    category: str | None = None
    action: str | None = None
    proposal: dict | None = None
    approval_reasons: list[str] = []
    decision: dict | None = None
    refund: dict | None = None
    tool_calls: list[dict] = []
    errors: list[str] = []
    prompt_versions: dict[str, str] = {}


@app.get("/health")
def health() -> dict:
    idx: PolicyIndex = state["index"]
    return {"status": "ok", "embedder": idx.embedder.name, "chunks": len(idx.chunks)}


@app.post("/policies/search", response_model=list[PolicyHit])
def search_policies(body: SearchIn):
    hits = state["index"].search(body.query, k=body.k)
    return [PolicyHit(doc=h.chunk.doc, title=h.chunk.title, section=h.chunk.section,
                      text=h.chunk.text, score=round(h.score, 4)) for h in hits]


@app.post("/cases", response_model=CaseOut, status_code=201)
def create_case(body: CaseIn):
    case_id = f"K{uuid.uuid4().hex[:10]}"
    _run(case_id, {"claim": body.claim, "customer_id": body.customer_id})
    return _case(case_id)


@app.get("/cases/{case_id}", response_model=CaseOut)
def get_case(case_id: str):
    return _case(case_id)


@app.post("/cases/{case_id}/decision", response_model=CaseOut)
def decide_case(case_id: str, body: Decision):
    # RF-007: resume the paused run from the approval node with the supervisor's decision.
    if _case(case_id).status != "pending_approval":
        raise HTTPException(409, "Case is not pending approval")
    _run(case_id, Command(resume=body.model_dump()))
    return _case(case_id)


def _graph():
    if state["graph"] is None:
        raise HTTPException(503, "Agent disabled: no LLM credentials configured")
    return state["graph"]


def _config(case_id: str) -> dict:
    return {"configurable": {"thread_id": case_id}}


def _run(case_id: str, graph_input) -> None:
    try:
        _graph().invoke(graph_input, _config(case_id))
    except PROVIDER_ERRORS as e:
        raise HTTPException(503, f"LLM provider unavailable: {type(e).__name__}") from e


def _case(case_id: str) -> CaseOut:
    snap = _graph().get_state(_config(case_id))
    v = snap.values
    if not v:
        raise HTTPException(404, "Case not found")
    decision = v.get("decision")
    if snap.interrupts:
        status = "pending_approval"
    elif decision:
        status = "approved" if decision.approved else "rejected"
    else:
        status = "proposed"
    proposal = v.get("proposal")
    return CaseOut(
        id=case_id, status=status,
        # Enums come back as plain strings from Postgres (see graph.SERDE).
        category=str(Category(v["category"]).value) if v.get("category") else None,
        action=str(Action(v["action"]).value) if v.get("action") else None,
        proposal=proposal.model_dump(mode="json") if proposal else None,
        approval_reasons=v.get("approval_reasons", []),
        decision=decision.model_dump() if decision else None,
        refund=v.get("refund"), tool_calls=v.get("tool_calls", []),
        errors=v.get("errors", []), prompt_versions=v.get("prompt_versions", {}))
