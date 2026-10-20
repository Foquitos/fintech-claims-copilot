# Design decisions

Each entry records what was decided, why, and which alternatives were discarded.

## 2026-10-14 · One OpenAI client for Azure OpenAI and OpenAI

**Decision.** `app/llm.py` builds a plain `OpenAI` client. With `AZURE_OPENAI_ENDPOINT` set it
points at Azure's OpenAI-compatible v1 API (`<endpoint>/openai/v1/`) and model names are
deployment names; otherwise it talks to OpenAI. Embeddings reuse the same client.

**Discarded.**
- `AzureOpenAI` client: needs a pinned `api_version` and a second code path.
- `langchain-openai` chat models: one more abstraction layer and dependency; LangGraph nodes
  are plain functions and do not need it.

## 2026-10-14 · The graph depends on a `StructuredLLM` protocol

**Decision.** Nodes call `llm.generate(system, user, schema)` and get a validated Pydantic
object back (OpenAI structured outputs). The LLM is injected into `build_graph`, so tests use a
fake and CI spends no tokens; another provider can be added for model comparisons.

**Discarded.** Parsing free-form JSON from the reply (needs extra validation and retries for
malformed output that structured outputs already prevent).

## 2026-10-14 · Unusable classifier output routes to a human; provider errors propagate

**Decision.** If the classifier refuses or returns something that does not validate, the claim
becomes `other` with confidence 0 and the error is recorded: no tools run and the action is
`escalate_operations`. Timeouts and connection errors are not caught, so the API can answer 503.

**Discarded.** Failing the request on bad output (the operator gets nothing useful) and
catching every exception (would hide an outage behind a made-up classification).

## 2026-10-14 · Prompts are versioned files with a content hash

**Decision.** `prompts/<name>.v<N>.md`; a new wording is a new file. The state records
`<name>.v<N>@<sha12>` for every node, so traces and eval results point to the exact text used.

**Discarded.** Prompts as Python string constants (changes hide in code diffs and are not
traceable from results).

## 2026-10-14 · Azure OpenAI lives outside the Terraform-managed demo stack

**Decision.** The Azure OpenAI account and its deployments sit in their own resource group in
`eastus2`. Pay-as-you-go deployments cost nothing while idle, so they stay up while the demo
stack is created and destroyed per session.

**Discarded.** Creating it with the rest of the stack (every destroy would delete the
deployments and the quota allocation).

## 2026-10-14 · Action glossary: one action per team or operation

**Decision.** Actions are a flat enum of nine values: `refund`, `escalate_operations`,
`escalate_fraud`, `escalate_security`, `escalate_promotions`, `block_card`, `inform_deadline`,
`request_info`, `reject`. The last three added cover policies that had no matching action
(06 cashback over 15 days, 07 suspicious messages, 08 card block). Out-of-scope claims stay
`escalate_operations`, not `reject`: the classifier also routes its own failures there, and a
failed classification must reach a person, not close a legitimate claim.

**Discarded.** A single `escalate` action plus a target-team field (EV-1 would compare two
fields, and a wrong team with the right action would count as correct); `reject` for
out-of-scope claims (see above).

## 2026-10-14 · Main model: gpt-4.1-mini (PA-2)

**Decision.** `gpt-4.1-mini` on Azure OpenAI (`eastus2`, GlobalStandard) is the main model.
On the 16-case dev set (`evals/dev_set.jsonl`, `python -m evals.model_selection`):

| Model | Classify accuracy | Classify latency (avg) | Classify cost / claim | Baseline action accuracy (PA-1) |
|---|---|---|---|---|
| gpt-4.1-mini | 16/16 | 0.97 s | USD 0.00018 | 10/16 (62%) |
| gpt-5.4-mini | 16/16 | 1.15 s | USD 0.00037 | 8/16 (50%) |

Same classification accuracy at half the cost and lower latency. Baseline action accuracy is
noise at this size: with the previous action glossary the ranking was reversed (6/15 vs 8/15).
Most baseline misses need policies or account data the baseline does not have; that is the gap
the agent must close. The choice is revisited on the hand-written golden set; switching is a
change of `LLM_MODEL`.

**Discarded.** `gpt-5.4-mini` as main (2x cost, no measured gain); nano models (not tried:
classification already saturates and the proposal step needs more reasoning).

**Note.** The dev set is written for model selection only and is not reused in the golden set,
so prompt changes are not tuned against the cases that judge them.

## 2026-10-20 · Policy retrieval queries with the raw claim, top 3

**Decision.** `retrieve_policies` searches the index with the claim text as written and keeps
the top 3 chunks (id, doc, section, text, score). That is the same query and `k` the retrieval
golden set measures (EV-3 is hit@3), so the eval scores exactly what the proposal will see.

**Discarded.** Prefixing the classified category to the query (it might help short claims, but
it would no longer be the query EV-3 measures; revisit with Azure embeddings if hit@3 drops
on real claims); a score threshold (no data yet to set one, and the proposal step must cite
anyway).

**Note.** With the offline `hash` embedder, a short claim such as "Me cobraron dos veces las
zapatillas" ranks the unrecognized-purchase policy first. Graph tests therefore check that
the top 3 reach the state, not which policy wins; ranking quality belongs to EV-3.

## 2026-10-20 · core-mock tools: read-only client, failures become actions

**Decision.** `app/tools/core.py` wraps core-mock in a small `CoreClient` (httpx, 5 s timeout,
`CORE_URL`), injected into the graph like the LLM. `lookup_customer` always reads the customer
and reads the last 30 days of transactions only for categories that depend on movements (card
block and security do not). A 404 becomes `request_info` (AC-003.2); a timeout, connection
error or 5xx becomes `escalate_operations` (PRD 9.1.6). Every call, failed or not, is appended
to `tool_calls` in the state so the trace shows what was checked (AC-003.1).

**Discarded.** Letting the LLM choose tools through function calling (the lookups are
deterministic per category, and a fixed path is cheaper, testable without tokens and cannot be
talked into extra calls); a separate fake class in tests (tests use the real client over
`httpx.MockTransport`, so HTTP error mapping is exercised too); `urllib` instead of httpx (it
was already pinned for tests, now it is a runtime dependency).

## 2026-10-20 · Proposal: the model drafts, the graph validates

**Decision.** `propose` asks the model for a `ProposalDraft` (action, amount, transaction id,
cited policy ids, customer reply, confidence); the category is copied from `classify`, so the
two cannot disagree. A refund without a target or with a non-positive amount fails schema
validation and is retried; stray amounts on other actions are dropped. Malformed output is
retried up to 2 times (3 attempts, each recorded in `errors`), then the case escalates to
Operations (RF-011). A separate `validate` node then applies checks in code, and any failure
sets `escalate_operations`, `needs_review` and a readable reason:

- citations must be ids of the retrieved excerpts; others are dropped and, if none remain, the
  proposal is uncited (RF-005, AC-005.1/2);
- a refund must target a looked-up transaction and not exceed its amount;
- below 0.6 confidence (the lower of classify and propose), a non-escalation action becomes an
  escalation (PRD 9.1.6). Existing escalations are kept: they already go to a person.

Approval limits stay out of both the prompt and this node: `core-mock` enforces them, and the
RF-006 interrupt decides when to pause.

**Discarded.** Retrying on failed citations or grounding (an uncited answer is a signal for a
human, not noise to resample); validating inside `propose` (a separate node makes the
before/after visible in the trace); asking the model for the category again.

**Note.** When `validate` overrides the action, `customer_reply` still reflects the model's
original intent; `needs_review` tells the support agent not to send it as is.

## 2026-10-20 · Human approval: interrupt in the graph, limits enforced again in core-mock

**Decision.** After `validate`, the graph pauses with `interrupt()` in an `approval` node when
the action is a refund above $50,000, a refund above $20,000 on an account younger than 90 days
(age measured against the core's date), or `escalate_fraud` (RF-006). The checkpointer keeps
the case: each case is a LangGraph thread, and `POST /cases/{id}/decision` resumes it with
`Command(resume=decision)` (RF-007). Only an approved refund continues to `execute`, the single
write tool. core-mock applies the same limits on its own: `POST /refunds` above them always
returns `pending_approval`, and the refund is released only by `POST /refunds/{id}/approve`,
which agent-api calls only after a supervisor approved. If the graph and core-mock ever
disagree, core-mock wins and the case is flagged for review.

**Discarded.** Creating the refund before the pause and approving it later (AC-006.1 requires
that nothing is written while pending); a supervisor flag on `POST /refunds` (one call could
both create and approve); limits in the prompt (design invariant).

## 2026-10-20 · Checkpoints in a separate `agent` database

**Decision.** `PostgresSaver` keeps checkpoints in an `agent` database inside the same Postgres
container, created by `docker/postgres-init/`. Without `CHECKPOINT_DB_URL`, agent-api falls back
to an in-memory saver and logs a warning. The serializer allowlists the state classes it may
rebuild, since LangGraph is moving to blocking any other.

**Discarded.** core-mock's database (it would mix the bank's data with the agent's state); a
second Postgres container (more moving parts for a demo).

**Note.** PostgresSaver stores `str` values inline as JSON, so the `str`-based enums `Category`
and `Action` come back as plain strings after a resume. The in-memory saver keeps them typed,
so only the end-to-end run against Postgres exposed it; nodes normalize these fields.

## 2026-10-20 · The proposal sees the core's date (propose.v2)

**Decision.** `lookup_customer` reads the core's date (`/health`), and `propose.v2` receives it
as `today` with one extra rule: use it to compute elapsed time before applying a deadline.
Policies depend on elapsed time (15 days for cashback, 48 hours for transfers), and core-mock's
data is anchored to a fixed date.

**Discarded.** The system clock (with fixed mock data every deadline would look expired);
leaving it to the model (it guessed).

## 2026-10-20 · One refund per transaction, enforced by core-mock

**Decision.** `POST /refunds` answers 409 if the transaction already has a refund, pending or
approved. The end-to-end run created two refunds for the same charge: one from a request that
failed after the write, one from the retry. Like the approval limits, this belongs to the
system of record, not to the agent: any caller, retry or duplicated claim is covered. In the
graph, the 409 is a core-mock error, so the case escalates to Operations without retrying.

**Discarded.** Deduplicating in agent-api (it only covers this caller, and two cases for the
same claim are different threads); an idempotency key per request (it covers retries of the
same request but not the same claim submitted twice).
