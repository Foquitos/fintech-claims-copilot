# AGENTS.md

Guide for coding agents working in this repo.

## What this is

A claims copilot for support agents at **Lumen**, a fictional digital wallet. A LangGraph agent classifies the claim, retrieves the applicable policy (RAG), checks transactions in `core-mock` and proposes a reply and an action, with human approval for risky actions. All data is made up.

- Specification: [docs/PRD.md](docs/PRD.md) (requirements RF/RNF, evals EV, phases in §12).
- Local instructions: if `AGENTS.local.md` exists at the repo root, read it before starting and follow it. It is machine-specific and not versioned.

## Status

- **Phase 1 (done):** `core-mock`, 8 policies, RAG with FAISS, retrieval golden set, Docker, CI.
- **Next: Phase 2** — LangGraph agent (RF-001 to RF-007, RF-011). Phases 3–6 in `docs/PRD.md` §12.

## Layout

```
services/
  core-mock/    FastAPI + SQLAlchemy + PostgreSQL. Customers, transactions, refunds. Enforces approval limits.
  agent-api/    FastAPI. RAG (app/rag/), evals (evals/), Spanish policies (policies/). The agent graph goes here.
  guardrails/   Placeholder for phase 3: prompt injection, masking, output validation.
docs/PRD.md     Initial specification.
```

## Commands

```bash
cd services/<svc> && pip install -r requirements-dev.txt && pytest -q
cd services/agent-api && python -m evals.retrieval     # retrieval metrics, offline
cd services/agent-api && python -m evals.agent         # spends tokens: only when asked
docker compose up --build                              # core-mock :8001, agent-api :8000
terraform -chdir=infra plan                            # from phase 5
```

## Design invariants (do not break)

- **Approval limits are enforced by `core-mock`, never by the prompt.** A refund > $50,000 (or > $20,000 on accounts younger than 90 days) stays `pending_approval` even if the model is manipulated.
- **CI spends no tokens.** CI uses `EMBEDDINGS_PROVIDER=hash` (deterministic lexical embedder). Graph tests use a fake LLM. Paid suites run only when prompts, the graph or policies change.
- **The index records which embeddings model built it** and fails if queried with another one.
- **Guardrails fail closed:** if the service does not respond, the claim is rejected.
- **Sensitive data is masked:** DNI, CBU and card numbers never reach the LLM, traces or replies in full.
- **Every proposal cites retrieved policies;** without a valid citation the action becomes "escalate".

## Conventions

- Python 3.11, FastAPI, Pydantic, SQLAlchemy 2, pytest.
- Code, comments, API values, commits and docs in English. Domain content stays in Spanish: policies, claims, golden sets and seed data (names, merchants, descriptions).
- Prompts live in `prompts/` as versioned files.
- New design decisions go to `docs/decisions.md`, with the alternatives that were discarded.
- Run `pytest -q` for every service you touched before calling something done.

## Ask before

- Adding dependencies or changing the database schema.
- Changing prompts in ways that affect evals.
- Touching `infra/` (Terraform) or deploy workflows.
- Any command that creates billable Azure resources or spends LLM tokens.
- Writing the golden set or the red team: the author writes those by hand.

## Never

- Commit `.env`, keys, `terraform.tfstate`, Azure credentials or any gitignored local file.
- Move approval-limit logic from `core-mock` into the prompt.
- Lower eval thresholds to make CI pass.
- Run `terraform apply` without being asked.
