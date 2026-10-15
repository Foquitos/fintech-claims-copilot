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
