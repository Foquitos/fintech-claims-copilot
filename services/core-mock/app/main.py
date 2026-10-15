"""core-mock: simulated core banking API for the Lumen wallet (customers and transactions).

It is the "source of truth" the agent queries through its tools. There is no real business
logic: it only reads made-up data and records refund requests.
"""
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import Customer, RefundRequest, RefundStatus, Transaction
from .seed import REFERENCE_DATE, seed

# Refunds above this amount stay pending until a human approves them.
AUTO_APPROVAL_LIMIT = Decimal("50000")


@asynccontextmanager
async def lifespan(_: FastAPI):
    with SessionLocal() as s:
        seed(s)
    yield


app = FastAPI(title="core-mock · Lumen wallet", version="0.1.0", lifespan=lifespan)


def get_db():
    with SessionLocal() as s:
        yield s


Db = Annotated[Session, Depends(get_db)]


def mask_dni(dni: str) -> str:
    return "*" * (len(dni) - 3) + dni[-3:]


class CustomerOut(BaseModel):
    id: str
    full_name: str
    dni_masked: str
    segment: str
    created_at: datetime
    card_blocked: bool


class TransactionOut(BaseModel):
    id: str
    customer_id: str
    created_at: datetime
    type: str
    status: str
    amount: Decimal
    counterparty: str
    description: str


class RefundIn(BaseModel):
    transaction_id: str
    amount: Decimal = Field(gt=0)
    reason: str = Field(min_length=5, max_length=200)


class RefundOut(BaseModel):
    id: str
    transaction_id: str
    amount: Decimal
    status: str


def _customer_out(c: Customer) -> CustomerOut:
    return CustomerOut(id=c.id, full_name=c.full_name, dni_masked=mask_dni(c.dni),
                       segment=c.segment, created_at=c.created_at, card_blocked=c.card_blocked)


def _tx_out(t: Transaction) -> TransactionOut:
    return TransactionOut.model_validate(t, from_attributes=True)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "reference_date": REFERENCE_DATE.isoformat()}


@app.get("/customers/{customer_id}", response_model=CustomerOut)
def get_customer(customer_id: str, db: Db):
    c = db.get(Customer, customer_id)
    if not c:
        raise HTTPException(404, "Customer not found")
    return _customer_out(c)


@app.get("/customers/{customer_id}/transactions", response_model=list[TransactionOut])
def list_transactions(
    customer_id: str,
    db: Db,
    days: Annotated[int, Query(ge=1, le=180)] = 30,
    type: str | None = None,
    status: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    if not db.get(Customer, customer_id):
        raise HTTPException(404, "Customer not found")
    since = REFERENCE_DATE - timedelta(days=days)
    q = (select(Transaction)
         .where(Transaction.customer_id == customer_id, Transaction.created_at >= since)
         .order_by(Transaction.created_at.desc())
         .limit(limit))
    if type:
        q = q.where(Transaction.type == type)
    if status:
        q = q.where(Transaction.status == status)
    return [_tx_out(t) for t in db.scalars(q)]


@app.get("/transactions/{transaction_id}", response_model=TransactionOut)
def get_transaction(transaction_id: str, db: Db):
    t = db.get(Transaction, transaction_id)
    if not t:
        raise HTTPException(404, "Transaction not found")
    return _tx_out(t)


@app.post("/refunds", response_model=RefundOut, status_code=201)
def create_refund(body: RefundIn, db: Db):
    t = db.get(Transaction, body.transaction_id)
    if not t:
        raise HTTPException(404, "Transaction not found")
    if body.amount > t.amount:
        raise HTTPException(422, "Refund cannot exceed the transaction amount")
    status = RefundStatus.APPROVED if body.amount <= AUTO_APPROVAL_LIMIT else RefundStatus.PENDING_APPROVAL
    r = RefundRequest(id=f"R{uuid.uuid4().hex[:10]}", transaction_id=t.id, amount=body.amount,
                      reason=body.reason, status=status, created_at=datetime.now())
    db.add(r)
    db.commit()
    return RefundOut(id=r.id, transaction_id=r.transaction_id, amount=r.amount, status=r.status)
