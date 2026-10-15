from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class TxType(StrEnum):
    PURCHASE = "purchase"
    TRANSFER_OUT = "transfer_out"
    TRANSFER_IN = "transfer_in"
    QR_PAYMENT = "qr_payment"
    CASHBACK = "cashback"
    REFUND = "refund"


class TxStatus(StrEnum):
    APPROVED = "approved"
    PENDING = "pending"
    REJECTED = "rejected"
    REVERSED = "reversed"


class RefundStatus(StrEnum):
    APPROVED = "approved"
    PENDING_APPROVAL = "pending_approval"


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    full_name: Mapped[str] = mapped_column(String(120))
    # The full DNI (national ID) never leaves the API: it is only exposed masked.
    dni: Mapped[str] = mapped_column(String(12))
    segment: Mapped[str] = mapped_column(String(20))  # standard | premium
    created_at: Mapped[datetime] = mapped_column(DateTime)
    card_blocked: Mapped[bool] = mapped_column(default=False)

    transactions: Mapped[list["Transaction"]] = relationship(back_populates="customer")


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    type: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    counterparty: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(String(160), default="")

    customer: Mapped[Customer] = relationship(back_populates="transactions")


class RefundRequest(Base):
    __tablename__ = "refund_requests"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    reason: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30))  # RefundStatus
    created_at: Mapped[datetime] = mapped_column(DateTime)
