"""Typed state shared by the graph nodes.

Each node returns a partial dict; LangGraph merges it into the state. With a checkpointer the
state is saved after every node, which is what lets a case wait for a supervisor (RF-006).
"""
import operator
from enum import Enum
from typing import Annotated, TypedDict

from pydantic import BaseModel, Field, model_validator


def _merge(a: dict, b: dict) -> dict:
    return {**a, **b}


class Category(str, Enum):
    DUPLICATE_CHARGE = "duplicate_charge"
    TRANSFER_NOT_CREDITED = "transfer_not_credited"
    UNRECOGNIZED_PURCHASE = "unrecognized_purchase"
    QR_PAYMENT = "qr_payment"
    CASHBACK = "cashback"
    CARD_BLOCK = "card_block"
    SECURITY = "security"
    OTHER = "other"


class Action(str, Enum):
    REFUND = "refund"
    ESCALATE_OPERATIONS = "escalate_operations"
    ESCALATE_FRAUD = "escalate_fraud"
    ESCALATE_SECURITY = "escalate_security"
    ESCALATE_PROMOTIONS = "escalate_promotions"
    BLOCK_CARD = "block_card"
    INFORM_DEADLINE = "inform_deadline"
    REQUEST_INFO = "request_info"
    REJECT = "reject"


class Classification(BaseModel):
    """What the classifier must return (RF-001)."""

    category: Category
    confidence: float = Field(ge=0, le=1)


class ProposalDraft(BaseModel):
    """What the proposal step must return (RF-004). The category comes from classify."""

    action: Action
    amount: float | None = Field(description="Refund amount in ARS; null unless action is refund")
    transaction_id: str | None = Field(description="Refunded transaction; null unless refund")
    cited_policies: list[str] = Field(description="Ids of the policy excerpts that support it")
    customer_reply: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _refund_fields(self) -> "ProposalDraft":
        if self.action == Action.REFUND:
            # A refund without a target is unusable output: it is retried (RF-011).
            if not self.transaction_id or not self.amount or self.amount <= 0:
                raise ValueError("refund needs transaction_id and a positive amount")
        else:
            # Stray values on other actions are harmless; drop them instead of retrying.
            self.amount, self.transaction_id = None, None
        return self


class Proposal(ProposalDraft):
    """The validated proposal the support agent sees: the draft after the graph's checks."""

    category: Category
    needs_review: bool = False
    review_reasons: list[str] = []


class Decision(BaseModel):
    """A supervisor's decision on a paused case (RF-007)."""

    approved: bool
    supervisor: str = Field(min_length=1, max_length=80)
    note: str | None = Field(default=None, max_length=500)


class ClaimState(TypedDict, total=False):
    # Input
    claim: str
    customer_id: str
    # classify
    category: Category
    confidence: float
    # retrieve_policies (RF-002): the chunks a proposal may cite
    policies: list[dict]
    # lookup_customer (RF-003): what core-mock returned (DNI already masked by core-mock)
    customer: dict
    transactions: list[dict]
    today: str  # the core's date, so deadlines are computed against its clock
    # propose and validate (RF-004/005/011)
    proposal: Proposal
    propose_attempts: int
    # approval (RF-006/007): why the case needs a supervisor, and what they decided
    approval_reasons: list[str]
    decision: Decision
    # execute: the refund as core-mock recorded it
    refund: dict
    # The final action: out_of_scope, lookup_customer on failure, propose after 3 invalid
    # outputs, or the validated proposal's
    action: Action
    # Observability: which node ran with which prompt version, and any recoverable error.
    # Annotated reducers make nodes add to these fields instead of overwriting them.
    prompt_versions: Annotated[dict[str, str], _merge]
    errors: Annotated[list[str], operator.add]
    # Every tool call with its arguments and outcome, so the trace shows what was checked.
    tool_calls: Annotated[list[dict], operator.add]
