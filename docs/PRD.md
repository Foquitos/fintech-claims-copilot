---
project: "Fintech Claims Copilot"
prd_id: "PRD-fintech-claims-copilot"
version: "0.2"
status: "Initial specification"
project_type: [ai, software]
owner: "Ignacio Otranto"
created: "2026-10-14"
---

# PRD: Fintech Claims Copilot

> This is the requirements document the project started from: the initial request, scope and acceptance criteria as defined on 2026-10-14. It is kept as a record of what was asked for; current status lives in the [README](../README.md) and later decisions in `docs/decisions.md`.

## 0. Document control

| Field | Value |
|---|---|
| ID | PRD-fintech-claims-copilot |
| Version | 0.2 |
| Status | Initial specification (Phase 1 delivered) |
| Project type | AI / Software |
| Owner | Ignacio Otranto |
| Related documents | [README](../README.md), [AGENTS.md](../AGENTS.md) |

**Change history**

| Version | Date | Change |
|---|---|---|
| 0.1 | 2026-10-14 | First draft with AWS + Terraform, pgvector and LLM observability. |
| 0.2 | 2026-10-14 | Cloud switched to Azure: Azure OpenAI, Container Apps, PostgreSQL Flexible Server, Key Vault and Terraform `azurerm`. Azure AI Search added as an option. |

## 1. Summary

A copilot for support agents at a fictional digital wallet ("Lumen") that classifies claims, retrieves the applicable policy, checks the customer's transactions and proposes a reply and an action, with human approval for risky actions.

The goal is to show end to end how an LLM agent is built, evaluated, secured and deployed in a domain with strict rules and sensitive data: LangGraph, OpenAI models served by Azure OpenAI, microservices, pgvector, evals in CI, a prompt-injection red team, observability, Terraform on Azure and Kubernetes.

## 2. Problem

**Situation (fictional scenario):** Lumen's support agents receive claims in free text with incomplete data ("I was charged twice for the sneakers"). To resolve each one they must identify the claim type, find the policy that applies, review the customer's transactions and decide on an action.

**Pain:**
- Answers depend on who handles the claim: the same situation gets different actions.
- Finding the right policy and cross-checking it with transactions takes time.
- Some actions move money (refunds) or carry risk (possible fraud) and need a control that does not depend on in-the-moment judgment.

**Why this domain:** a fintech combines explicit business rules, sensitive data (national ID/DNI, bank account/CBU, cards) and actions with financial impact. It is a demanding case for an agent: it must cite sources, resist manipulation and keep risk decisions outside the model.

**Base cases:** `core-mock` customers `C0001`–`C0006` represent the typical claims (duplicate charge, pending transfer, possible fraud, reversed QR payment, missing cashback, large refund).

## 3. Goals and success metrics

| ID | Goal | Metric | Baseline | Target | How it is measured |
|---|---|---|---|---|---|
| OBJ-1 | Agent picks the right action | Accuracy of the proposed action on the agent golden set (EV-1) | Not measured (OQ-1) | ≥ 85% | Eval suite in CI |
| OBJ-2 | Agent resists manipulation | Red-team attacks that achieve an unauthorized action (EV-4) | Not measured | 0 of 30 | Red-team suite in CI |
| OBJ-3 | Reproducible Azure deployment | Time from a clean `terraform apply` to the service responding | No deployment | ≤ 30 min, no manual steps outside the README | Deployment test from a clean account |
| OBJ-4 | Portable architecture | Environments where the three services run with the same images | Local only (docker compose) | docker compose, kind and Azure | Test in each environment |

**Counter-metrics:**

| ID | Metric | Threshold | Balances |
|---|---|---|---|
| CM-1 | Azure OpenAI cost per processed claim | ≤ USD 0.02 | OBJ-1 |
| CM-2 | Monthly Azure spend | ≤ USD 25 (infrastructure destroyed when not in use) | OBJ-3 |
| CM-3 | p95 latency of a full agent request | ≤ 15 s | OBJ-1 |

## 4. Scope

### 4.1 In scope

- Three microservices: `core-mock` (wallet data), `agent-api` (RAG + agent) and `guardrails` (input and output validation).
- LangGraph agent with OpenAI models served by Azure OpenAI: classification, RAG over policies, tools against `core-mock` and human approval with persistent state.
- pgvector on PostgreSQL as the vector store; FAISS remains only as an offline option for tests.
- Evaluation suites in CI: retrieval, agent (golden set) and prompt-injection red team.
- Traces for every request (tokens, cost, latency, nodes) in Langfuse.
- Azure deployment with Terraform and deploys from GitHub Actions.
- Optional Azure AI Search adapter as an alternative vector store (RF-020).
- Kubernetes manifests runnable on a local cluster (kind).
- README with architecture, decisions, eval results and a demo GIF.

### 4.2 Non-goals

- No real users or real data: everything is fictional and there is no agent login.
- No integration with real payment providers, banks or card networks; refunds are only recorded in `core-mock`.
- No fine-tuning or model training.
- No elaborate frontend: the interface is the interactive API docs and, at most, a minimal page (RF-018).
- Azure infrastructure is not kept running permanently: it is brought up for demos and destroyed.
- No managed Kubernetes (AKS): the cloud deployment uses Azure Container Apps for cost reasons; Kubernetes is demonstrated locally.
- No multi-language support: claims and policies are in Spanish; code and documentation in English.

## 5. Users

### 5.1 Users

| User / role | Context | Needs | Frequency |
|---|---|---|---|
| Support agent (fictional) | Handles Lumen customer claims | A suggested reply and action with the policy cited | Per claim |
| Supervisor (fictional) | Approves risky actions | See the case and proposed action, then approve or reject | Per escalated case |
| Technical reviewer | Opens the repo to understand the project; spends 5–10 minutes | Understand from the README what it does, how it is built, how it is evaluated and that it runs | Once |

### 5.2 Key journeys

- **UJ-1. Support agent resolves a duplicate charge**
  - Start: the agent receives "me cobraron dos veces las zapatillas" ("I was charged twice for the sneakers") from customer C0001.
  - Steps: submits the claim and customer ID → the copilot classifies it, checks transactions and retrieves the policy → proposes refunding the second charge, citing the policy → since the amount is ≤ $50,000, the refund is approved in `core-mock`.
  - Outcome: a reply ready for the customer and a request number.
  - Failure: if it does not find two identical charges, it does not propose a refund and routes it as an unrecognized purchase.
- **UJ-2. Supervisor approves a large refund**
  - Start: claim from C0006 for $480,000.
  - Steps: the copilot proposes a refund → the system interrupts the flow and leaves the case pending → the supervisor reviews and approves or rejects → the agent resumes and records the outcome.
  - Outcome: the refund only exists with a recorded approval.
  - Failure: if the process restarts while waiting, the case is still pending when it comes back.
- **UJ-3. Technical reviewer evaluates the repo**
  - Start: link to the repository.
  - Steps: reads the README → looks at the diagram and decisions → sees the eval results table and green CI → optionally runs `docker compose up`.
  - Outcome: understands the project in under 10 minutes.
  - Failure: if the Azure demo is down, the README shows the GIF and how to bring it up.

## 6. Glossary

- **Claim**: customer message the support agent enters into the system, with the customer ID.
- **Category**: claim type; one of: duplicate charge, transfer not credited, unrecognized purchase, QR payment, cashback, card block, security question, other.
- **Policy**: Lumen support rules document; lives in `policies/` (in Spanish).
- **Action**: decision proposed by the agent; one of: refund, escalate to Operations, escalate to Fraud, inform timeline, request data, reject.
- **Human approval**: the flow pauses until a supervisor approves or rejects the action.
- **Golden set**: claims with the correct category and action labeled by hand.
- **Red team**: claims that try to manipulate the agent (prompt injection).
- **Trace**: record of a request with the nodes visited, tokens, cost and latency.

## 7. Functional requirements

Priority: **Must** (no release without it) · **Should** (important, can be deferred) · **Could** (nice to have).

### RF-001: Claim classification

- **Requirement:** When the system receives a claim, it must assign exactly one category from the glossary.
- **Priority:** Must
- **Traceability:** UJ-1, OBJ-1, EV-2
- **Acceptance criteria:**
  - AC-001.1: Given the agent golden set, when the suite runs, then category accuracy is ≥ 90% (EV-2).
  - AC-001.2: Given a claim that fits no category ("what time is it?"), when processed, then the category is "other" and no tool is executed.

### RF-002: Policy retrieval

- **Requirement:** When the claim's category is not "other", the system must retrieve the most relevant policy chunks from the vector store.
- **Priority:** Must
- **Traceability:** UJ-1, OBJ-1, EV-3
- **Acceptance criteria:**
  - AC-002.1: Given the retrieval golden set, when the suite runs with Azure OpenAI embeddings, then hit@3 ≥ 95% (EV-3).
  - AC-002.2: Given the vector store is down, when a claim arrives, then the system returns a 503 with a clear message and does not make up a policy.

### RF-003: Customer data lookup

- **Requirement:** When the category requires looking at transactions, the system must query the customer and their transactions in `core-mock` through tools.
- **Priority:** Must
- **Traceability:** UJ-1, UJ-2, OBJ-1
- **Acceptance criteria:**
  - AC-003.1: Given C0001's claim, when processed, then the trace shows the call to the transactions tool and the reply identifies both charges by ID.
  - AC-003.2: Given a nonexistent customer ID, when processed, then the action is "request data" and no write tool is called.

### RF-004: Structured proposal

- **Requirement:** The system must return every proposal with a fixed schema: category, action, amount, transaction ID, cited policies, customer reply and confidence.
- **Priority:** Must
- **Traceability:** UJ-1, OBJ-1, EV-1
- **Acceptance criteria:**
  - AC-004.1: Given any golden-set case, when processed, then the output validates against the published schema.
  - AC-004.2: Given the golden set, when the suite runs, then the action matches the label in ≥ 85% of cases (EV-1).

### RF-005: Mandatory policy citations

- **Requirement:** If the proposal does not cite at least one retrieved policy, the system must change the action to "escalate" and flag it for review.
- **Priority:** Must
- **Traceability:** OBJ-1, EV-5
- **Acceptance criteria:**
  - AC-005.1: Given a model response without citations, when validated, then the final action is "escalate to Operations".
  - AC-005.2: Given a citation to a policy that was not among those retrieved, when validated, then it is treated as uncited.

### RF-006: Interrupt for human approval

- **Requirement:** If the action is a refund above $50,000, a refund above $20,000 on an account younger than 90 days, or an escalation for possible fraud, the system must pause the flow until a supervisor decides.
- **Priority:** Must
- **Traceability:** UJ-2, OBJ-2
- **Acceptance criteria:**
  - AC-006.1: Given C0006's claim ($480,000), when processed, then the case is "pending approval" and no refund is created.
  - AC-006.2: Given a $18,500 refund on a 900-day-old account, when processed, then there is no pause.

### RF-007: Resume after the decision

- **Requirement:** When a supervisor approves or rejects a pending case, the system must resume the flow from the pause point and record the decision.
- **Priority:** Must
- **Traceability:** UJ-2
- **Acceptance criteria:**
  - AC-007.1: Given an approved pending case, when resumed, then the refund is created and the reply includes the request number.
  - AC-007.2: Given a pending case and a service restart, when the case is queried, then it is still pending with the same state.

### RF-008: Limits enforced by the system

- **Requirement:** The system must create refunds only through `core-mock`, which enforces approval limits without depending on the model's output.
- **Priority:** Must
- **Traceability:** OBJ-2, EV-4
- **Acceptance criteria:**
  - AC-008.1: Given a manipulated model that tries to create a $480,000 refund without approval, when `core-mock` is called, then the refund is `pending_approval`.
  - AC-008.2: Given an amount larger than the transaction, when the refund is attempted, then `core-mock` returns 422.

### RF-009: Prompt-injection detection on input

- **Requirement:** When a claim arrives, the guardrails service must evaluate it and, if it detects a manipulation attempt, block it before it reaches the agent.
- **Priority:** Must
- **Traceability:** OBJ-2, EV-4
- **Acceptance criteria:**
  - AC-009.1: Given the 30-case red team, when the suite runs, then ≥ 90% is blocked at input and 0 produce an unauthorized action.
  - AC-009.2: Given the legitimate golden set, when the suite runs, then ≤ 5% of legitimate claims are blocked (false positives).

### RF-010: Sensitive data masking

- **Requirement:** The system must mask DNI, CBU and card numbers before sending text to the LLM and in every response.
- **Priority:** Must
- **Traceability:** OBJ-2
- **Acceptance criteria:**
  - AC-010.1: Given a claim with a 16-digit card number, when processed, then the LLM trace shows only the last 4 digits.
  - AC-010.2: Given a model response containing a full DNI, when the output is validated, then it is returned masked.

### RF-011: Model output validation

- **Requirement:** If the model output does not validate against the schema, the system must retry up to 2 times and, if it still fails, return the action "escalate" with the error recorded.
- **Priority:** Must
- **Traceability:** OBJ-1
- **Acceptance criteria:**
  - AC-011.1: Given a fake model that returns invalid JSON 3 times, when processed, then the action is "escalate" and the trace shows 3 attempts.
  - AC-011.2: Given a fake model that fails once and then answers correctly, when processed, then the second answer is used and the trace shows 2 attempts.

### RF-012: Agent evals in CI

- **Requirement:** When a pull request changes prompts, the graph or policies, CI must run the agent golden-set suite and fail if EV-1 drops below threshold.
- **Priority:** Must
- **Traceability:** OBJ-1
- **Acceptance criteria:**
  - AC-012.1: Given a PR that drops EV-1 to 80%, when CI runs, then the check fails with the cases that changed.
  - AC-012.2: Given a PR that touches neither prompts, graph nor policies, when CI runs, then the paid suite does not run (no tokens spent).

### RF-013: Red team in CI

- **Requirement:** When the agent suite runs, CI must also run the red team and fail if any case produces an unauthorized action.
- **Priority:** Must
- **Traceability:** OBJ-2, EV-4
- **Acceptance criteria:**
  - AC-013.1: Given a red-team case that obtains a refund without approval, when CI runs, then the check fails.
  - AC-013.2: Given the LLM provider does not respond during the run, when CI runs, then the check fails as "not evaluated", not as passed.

### RF-014: Per-request traces

- **Requirement:** The system must record, per request, the nodes visited, tokens, cost and latency in Langfuse.
- **Priority:** Must
- **Traceability:** CM-1, CM-3
- **Acceptance criteria:**
  - AC-014.1: Given a processed request, when Langfuse is opened, then the trace shows each node with tokens and cost.
  - AC-014.2: Given Langfuse is unavailable, when a request arrives, then it is still processed and the tracing error is logged.

### RF-015: Azure deployment with Terraform

- **Requirement:** The system must be fully deployable to Azure with `terraform apply` and removable with `terraform destroy`.
- **Priority:** Must
- **Traceability:** OBJ-3, CM-2
- **Acceptance criteria:**
  - AC-015.1: Given an Azure subscription with credentials configured, when `terraform apply` runs, then within 30 min the three services answer `/health` on the public URL.
  - AC-015.2: Given a `terraform destroy`, when it finishes, then the resource group no longer exists and Cost Management shows no usage the next day.

### RF-016: Continuous deployment from GitHub Actions

- **Requirement:** When code is merged to `main`, GitHub Actions must build the images, push them to Azure Container Registry and update the Container Apps, authenticating via OIDC (Entra ID federated credential) with no stored secrets.
- **Priority:** Should
- **Traceability:** OBJ-3
- **Acceptance criteria:**
  - AC-016.1: Given a merge to `main` with the infrastructure up, when the workflow finishes, then the new version answers on `/health`.
  - AC-016.2: Given the infrastructure is down, when there is a merge, then the deploy job is skipped without failing.

### RF-017: pgvector vector store

- **Requirement:** The system must store and query policy embeddings in PostgreSQL with pgvector.
- **Priority:** Must
- **Traceability:** OBJ-4, EV-3
- **Acceptance criteria:**
  - AC-017.1: Given the same golden set, when pgvector is compared with FAISS, then hit@3 is equal.
  - AC-017.2: Given a change of embeddings model, when the service starts, then it detects the difference and reindexes.

### RF-018: Minimal page for support agents

- **Requirement:** Where the demo is deployed, the system must offer a page to enter a claim, see the proposal and approve or reject pending cases.
- **Priority:** Could
- **Traceability:** UJ-1, UJ-2, UJ-3
- **Acceptance criteria:**
  - AC-018.1: Given C0006's claim, when submitted from the page, then it shows as pending and can be approved from the same page.

### RF-019: Kubernetes manifests

- **Requirement:** The repository must include Kubernetes manifests that bring up the three services and the database on a local kind cluster.
- **Priority:** Should
- **Traceability:** OBJ-4
- **Acceptance criteria:**
  - AC-019.1: Given an empty kind cluster, when `kubectl apply -k k8s/` runs, then pods become "Ready" with liveness and readiness probes.

### RF-020: Azure AI Search vector store

- **Requirement:** Where Azure AI Search is configured as the provider, the system must index and query policies in Azure AI Search instead of pgvector.
- **Priority:** Could
- **Traceability:** OBJ-4, EV-3
- **Acceptance criteria:**
  - AC-020.1: Given the retrieval golden set, when run against Azure AI Search (free tier), then hit@3 ≥ 95%.

## 8. Non-functional requirements

| ID | Category | Requirement (measurable) | How it is verified |
|---|---|---|---|
| NFR-001 | Performance | Full request without human approval: p95 ≤ 15 s; policy search: p95 ≤ 500 ms | Load script with 50 golden-set requests |
| NFR-002 | Security and access | Secrets only in Azure Key Vault (read via managed identity) or a local `.env` outside git; no container runs as root; deploys via OIDC | Review + `gitleaks` in CI |
| NFR-003 | Privacy and sensitive data | All data is fictional; DNI, CBU and card numbers never reach the LLM or traces in full | Automated test (RF-010) |
| NFR-004 | Availability and recovery | N/A — demo with no uptime commitment; brought up on demand | — |
| NFR-005 | Observability | 100% of requests traced in Langfuse; structured JSON logs sent to Application Insights on Azure | Trace review after running the suite |
| NFR-006 | Usability | A reviewer can run the project locally with ≤ 3 commands from the README | Test on a clean machine |
| NFR-007 | Maintainability | Test coverage ≥ 80% on own logic; README and ADRs in English; every decision in `docs/decisions.md` | `pytest --cov` in CI + review |
| NFR-008 | Cost | Models ≤ USD 15 across all development; Azure ≤ USD 25/month | Budget with alerts in Azure Cost Management |

## 9. Project-type modules

### 9.1 Artificial intelligence

#### 9.1.1 Why AI
Claims arrive in free text with incomplete information; classifying them, finding the policy and writing a reply consistent with fixed rules does not scale with hand-written rules.

#### 9.1.2 Model task
- Input: claim text (≤ 2,000 characters) and customer ID.
- Output: JSON with `category`, `action`, `amount`, `transaction_id`, `cited_policies[]`, `customer_reply`, `confidence`.
- Approach: tool-using agent in LangGraph (classify → retrieve policies → look up data → propose → validate → human approval when required).
- Primary and alternative model: a small OpenAI model deployed on Azure OpenAI (Azure AI Foundry) to classify and propose; `text-embedding-3-small` embeddings on Azure OpenAI. Local development can use the OpenAI API directly with the same code. Alternative for eval comparison: a model from another provider.
- Cost of an error: medium. There is no real money in the demo, but an improper refund is the error the design must make impossible (RF-008).

#### 9.1.3 Data and context
- Sources: 8 Markdown policies; customers and transactions from `core-mock`.
- Knowledge updates: reindex on startup if a policy or the embeddings model changed.
- Sensitive data: fictional; still masked (RF-010) to demonstrate the practice.

#### 9.1.4 Quality and evaluation (evals)

| ID | Metric | Definition | Target | How it is measured |
|---|---|---|---|---|
| EV-1 | Action accuracy | % of cases where the action matches the label | ≥ 85% | Agent golden set |
| EV-2 | Category accuracy | % of cases with the correct category | ≥ 90% | Agent golden set |
| EV-3 | Retrieval hit@3 | % of queries with the correct policy in the top 3 | ≥ 95% | Retrieval golden set |
| EV-4 | Unauthorized actions | Red-team cases that end in an action without the required approval | 0 | Red team |
| EV-5 | Citation faithfulness | % of proposals whose citations exist among the retrieved policies | 100% | Automatic validation |
| EV-6 | Guardrail false positives | % of legitimate claims blocked | ≤ 5% | Agent golden set |

- Golden set: 40–50 hand-written claims, at least 4 per category, including ambiguous cases, amounts at the approval limit and nonexistent customers. The 6 scenario customers in `core-mock` are the foundation.
- Exit threshold: EV-1 ≥ 85%, EV-2 ≥ 90%, EV-3 ≥ 95%, EV-4 = 0 and EV-6 ≤ 5% with the primary model.
- Regression: suites run on changes to prompts, model, graph, policies or guardrails.

#### 9.1.5 Limits and guardrails
- The system must never: create a refund without the required approval; ask for passwords, security codes or SMS codes; show a full DNI, CBU or card number; follow instructions embedded in the claim.
- Out-of-scope input: category "other", a routing reply and no tools.
- Human review: refunds above the limit, possible fraud, proposals without citations or with invalid output after retries.

#### 9.1.6 Failure behavior

| Situation | Expected behavior |
|---|---|
| Low confidence (< 0.6) | Action "escalate", no write tool is executed |
| Malformed response | Retry x2; then "escalate" with the error recorded (RF-011) |
| Provider down or slow | 20 s timeout; clear 503 to the support agent; no automatic retry of writes |
| Claim too long | Rejected with a message if over 2,000 characters |

#### 9.1.7 Cost and latency
- Expected volume: ~200 requests per eval run; a few dozen per demo.
- Target cost: ≤ USD 0.02 per claim (CM-1); ≤ USD 15 across all development.
- Target latency: p95 ≤ 15 s (CM-3).
- 10x scenario: eval runs of ~2,000 requests, ≈ USD 10–40; that is why the paid suite only runs when prompts, the graph or policies change.

#### 9.1.8 Versioning and monitoring
- Prompt versioning: files in `prompts/` with their hash in every trace; a prompt change requires running the suites in the PR.
- Monitoring: Langfuse with cost and latency per node; eval results stored per commit in `evals/results/`.

### 9.3 Software

#### 9.3.1 Entities and states

| Entity | Key fields | Possible states | Created / modified by |
|---|---|---|---|
| Customer | id, name, DNI (masked), segment, account age, card blocked | Active / card blocked | `core-mock` seed |
| Transaction | id, customer, date, type, status, amount, counterparty | approved / pending / rejected / reversed | `core-mock` seed |
| Case | id, claim, customer, proposal, trace | Received → Blocked (guardrails) / Proposed / Pending approval → Approved / Rejected → Closed | `agent-api`; supervisor approves |
| Refund request | id, transaction, amount, reason | approved / pending_approval | `core-mock`, at the agent's request |

#### 9.3.2 Roles and permissions

| Role | Can view | Can create / edit | Can delete / approve |
|---|---|---|---|
| Support agent (demo) | Their cases and proposals | Create cases | — |
| Supervisor (demo) | Pending cases | — | Approve or reject pending cases |

No real authentication (out of scope); the role is passed via a header in the demo.

#### 9.3.3 Screens and experience states
- `agent-api` interactive docs (`/docs`): normal, validation error (422), provider down (503), blocked by guardrails (403 with reason).
- Minimal page (RF-018, Could): claim form, proposal, pending list with approve/reject, loading and error states.

#### 9.3.4 Integrations and contracts

| System | Direction | Contract (endpoint / format) | Behavior on failure |
|---|---|---|---|
| core-mock | Read / write | `GET /customers/{id}`, `GET /customers/{id}/transactions`, `POST /refunds` (JSON) | 5 s timeout; the tool returns an error to the agent and the action becomes "escalate" |
| guardrails | Read | `POST /check/input`, `POST /check/output` (JSON) | If it does not respond, the claim is rejected (fail closed) |
| Azure OpenAI | Read | Chat completions and embeddings (Azure AI Foundry deployments) | 20 s timeout; 503 to the support agent |
| Langfuse | Write | SDK | Continue without a trace and log the error |
| PostgreSQL + pgvector | Read / write | SQLAlchemy | 503 on `/health` |

#### 9.3.5 Stack and environments
- Stack: Python 3.11, FastAPI, LangGraph, OpenAI SDK (Azure OpenAI client), PostgreSQL 16 + pgvector, SQLAlchemy 2, pytest, Docker, Terraform, GitHub Actions, Langfuse, kind.
- Environments: local (docker compose), CI (GitHub Actions, no tokens except paid suites) and Azure (demo).
- Azure deployment: Azure Container Apps (3 services, scale to zero), Azure Database for PostgreSQL Flexible Server with the pgvector extension, Azure Container Registry, Key Vault, Application Insights and Azure OpenAI; a single resource group; region `eastus2`; Terraform with the `azurerm` provider; deploys from GitHub Actions via OIDC.

#### 9.3.6 Guide for coding agents
See [AGENTS.md](../AGENTS.md).

## 10. Dependencies, constraints and assumptions

### 10.1 Dependencies

| ID | Dependency | Needed for |
|---|---|---|
| DEP-1 | Azure OpenAI resource with chat and embeddings model deployments (or an OpenAI API key for development) | Phase 2 |
| DEP-2 | Azure subscription with a budget and alerts in Cost Management | Phase 2 |
| DEP-3 | Langfuse Cloud account (free tier) or self-hosted | Phase 4 |
| DEP-4 | Public GitHub repository | Phase 2 |

### 10.2 Constraints

- Solo, part-time project.
- Budget: models ≤ USD 15 total; Azure ≤ USD 25/month.
- All data is fictional; no information from real companies or customers is used.
- Repo, code and documentation in English.

### 10.3 Assumptions

| ID | Assumption | If false |
|---|---|---|
| AS-1 | Free account credits and Container Apps free grants cover most of the cost; PostgreSQL Flexible Server on the Burstable B1ms tier fits the budget | Stop the database server outside demos and destroy everything after each session |
| AS-2 | A small OpenAI model on Azure reaches EV-1 ≥ 85% | Try a larger model for the proposal step only, keeping CM-1 in check |

## 11. Risks

| ID | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R-1 | Unexpected Azure spend from forgotten resources | Medium | Medium | Budget alerts at USD 10 and 20; everything in one resource group; `terraform destroy` at the end of every session |
| R-2 | Scope creep keeps the project from reaching a demoable state | Medium | High | Publish the repo from Phase 1; every phase leaves something demoable |
| R-3 | A golden set without hard cases inflates results | Medium | Medium | Include ambiguous and boundary cases; publish the failing cases |
| R-4 | AI-assisted code that is not deeply understood | Medium | High | Review every PR; document and explain every decision in `docs/decisions.md` |

## 12. Delivery plan

| Phase | Scope (RFs included) | Exit criteria | Target date |
|---|---|---|---|
| 1 | Foundation: `core-mock`, policies, RAG with FAISS, retrieval golden set, Docker, CI | 14 tests green; offline hit@3 = 100% | 2026-10-13 (done) |
| 2 | RF-001 to RF-007, RF-011 | Graph tests with a fake LLM green; UJ-1 and UJ-2 work end to end | 2026-10-28 |
| 3 | RF-008 to RF-010, RF-012, RF-013 | 40+ case golden set and 30-case red team; 9.1.4 thresholds met; CI gate | 2026-11-11 |
| 4 | RF-014, RF-017 | pgvector with the same hit@3 as FAISS; Langfuse traces for 100% of requests | 2026-11-18 |
| 5 | RF-015, RF-016, RF-020 (optional) | Clean `terraform apply` in ≤ 30 min; OIDC deploys; `destroy` leaves nothing behind | 2026-12-02 |
| 6 | RF-019, RF-018 (optional), final README | Pods Ready on kind; README with eval results and GIF | 2026-12-09 |

**Launch criteria:** public repo with green CI, eval thresholds met and published in the README, and an Azure deployment tested at least once.

**Rollback plan:** every phase is merged via PR; if a phase breaks evals or tests, the merge is reverted. On Azure, `terraform destroy` and fall back to the local demo.

## 13. Open questions

| ID | Question | Impact if unanswered |
|---|---|---|
| OQ-1 | What is the EV-1 baseline with a simple prompt and no graph? | No way to show how much the agent adds |
| OQ-2 | Which specific OpenAI model is deployed on Azure OpenAI as primary (cost vs. EV-1), and which region has quota? | Blocks Phase 2 |
| OQ-3 | Langfuse Cloud or self-hosted in the compose file? | Blocks Phase 4 |

## 14. Decision log

| ID | Date | Decision | Discarded alternatives |
|---|---|---|---|
| DEC-1 | 2026-10-13 | Fintech domain with a fictional wallet | Generic contact center (fewer business rules and less risk to control) |
| DEC-2 | 2026-10-14 | Cloud: Azure with Terraform (v0.2; replaces AWS from v0.1) | AWS (Azure brings OpenAI and AI Search into the same ecosystem); local only |
| DEC-3 | 2026-10-14 | Azure Container Apps in the cloud and Kubernetes only locally (kind) | AKS (nodes carry a fixed cost for a demo) |
| DEC-4 | 2026-10-14 | pgvector as the vector store; FAISS only for offline tests | FAISS only (no persistence or real operations); Pinecone (paid external service) |
| DEC-5 | 2026-10-14 | Repo in English | Spanish (limits the audience) |
| DEC-6 | 2026-10-13 | Approval limits are enforced by `core-mock`, not the prompt | Trusting the prompt (vulnerable to prompt injection) |
| DEC-7 | 2026-10-14 | Built with coding agents, reviewing every PR | Writing everything by hand |
| DEC-8 | 2026-10-14 | Models served by Azure OpenAI; same interface as the OpenAI API for local development | Direct OpenAI API only (no Azure layer) |
