"""Typed state shared by the graph nodes.

Each node returns a partial dict; LangGraph merges it into the state. Fields filled by later
nodes (policies, transactions, proposal) arrive with RF-002 to RF-007.
"""
import operator
from enum import Enum
from typing import Annotated, TypedDict

from pydantic import BaseModel, Field


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


class ClaimState(TypedDict, total=False):
    # Input
    claim: str
    customer_id: str
    # classify
    category: Category
    confidence: float
    # out_of_scope (and, from RF-004 on, the proposal)
    action: Action
    # Observability: which node ran with which prompt version, and any recoverable error.
    # Annotated reducers make nodes add to these fields instead of overwriting them.
    prompt_versions: Annotated[dict[str, str], _merge]
    errors: Annotated[list[str], operator.add]
