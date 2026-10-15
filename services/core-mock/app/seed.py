"""Made-up, deterministic data for the fictional "Lumen" wallet.

Customers C0001-C0006 each have a hand-crafted claim scenario that the evaluation golden
set relies on. Everything else is random transactions (fixed seed). Names, merchants and
descriptions are in Spanish because Lumen is an Argentine wallet.
"""
import random
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from .db import Base
from .models import Customer, Transaction, TxStatus, TxType

# Fixed reference date: the data does not depend on the day it runs.
REFERENCE_DATE = datetime(2026, 10, 10, 12, 0)

FIRST_NAMES = ["Lucía", "Martín", "Sofía", "Joaquín", "Valentina", "Tomás", "Camila", "Mateo",
               "Florencia", "Nicolás", "Julieta", "Federico", "Agustina", "Santiago", "Micaela"]
LAST_NAMES = ["González", "Rodríguez", "Fernández", "López", "Martínez", "Pérez", "Gómez",
              "Díaz", "Romero", "Sosa", "Álvarez", "Torres", "Ruiz", "Ramírez", "Acosta"]
MERCHANTS = ["Supermercado Del Parque", "Farmacia Central", "Estación de Servicio Ruta 9",
             "Librería Ateneo", "Café La Esquina", "Streaming Plus", "Tienda Deportiva Gol",
             "Ferretería El Tornillo", "Delivery Rápido", "Cine Avenida"]


def _money(value: float) -> Decimal:
    return Decimal(str(round(value, 2)))


class _Ids:
    def __init__(self) -> None:
        self.n = 0

    def next(self) -> str:
        self.n += 1
        return f"T{self.n:07d}"


def _scenario_customers(ids: _Ids) -> tuple[list[Customer], list[Transaction]]:
    d = REFERENCE_DATE
    customers = [
        Customer(id="C0001", full_name="Lucía Fernández", dni="30111222", segment="standard",
                 created_at=d - timedelta(days=900)),
        Customer(id="C0002", full_name="Martín Gómez", dni="28444555", segment="premium",
                 created_at=d - timedelta(days=1500)),
        Customer(id="C0003", full_name="Sofía Díaz", dni="35777888", segment="standard",
                 created_at=d - timedelta(days=400)),
        Customer(id="C0004", full_name="Joaquín Romero", dni="40123456", segment="standard",
                 created_at=d - timedelta(days=200)),
        Customer(id="C0005", full_name="Valentina Sosa", dni="33999000", segment="premium",
                 created_at=d - timedelta(days=700)),
        Customer(id="C0006", full_name="Tomás Acosta", dni="38222333", segment="standard",
                 created_at=d - timedelta(days=60)),
    ]
    tx = [
        # C0001: duplicate charge at the same merchant, same amount, 2 minutes apart.
        Transaction(id=ids.next(), customer_id="C0001", created_at=d - timedelta(days=2, minutes=2),
                    type=TxType.PURCHASE, status=TxStatus.APPROVED, amount=_money(18500),
                    counterparty="Tienda Deportiva Gol", description="Zapatillas running"),
        Transaction(id=ids.next(), customer_id="C0001", created_at=d - timedelta(days=2),
                    type=TxType.PURCHASE, status=TxStatus.APPROVED, amount=_money(18500),
                    counterparty="Tienda Deportiva Gol", description="Zapatillas running"),
        # C0002: outgoing transfer still pending after 3 days.
        Transaction(id=ids.next(), customer_id="C0002", created_at=d - timedelta(days=3),
                    type=TxType.TRANSFER_OUT, status=TxStatus.PENDING,
                    amount=_money(250000), counterparty="CBU destino ****4821",
                    description="Pago alquiler"),
        # C0003: foreign purchase the customer does not recognize (possible fraud).
        Transaction(id=ids.next(), customer_id="C0003", created_at=d - timedelta(days=1, hours=5),
                    type=TxType.PURCHASE, status=TxStatus.APPROVED, amount=_money(312000),
                    counterparty="ELECTRO STORE MIAMI US", description="Compra internacional"),
        # C0004: rejected QR payment whose debit was reversed.
        Transaction(id=ids.next(), customer_id="C0004", created_at=d - timedelta(hours=20),
                    type=TxType.QR_PAYMENT, status=TxStatus.REVERSED, amount=_money(9200),
                    counterparty="Café La Esquina", description="Pago QR"),
        # C0005: purchase that should have earned promo cashback and did not.
        Transaction(id=ids.next(), customer_id="C0005", created_at=d - timedelta(days=12),
                    type=TxType.PURCHASE, status=TxStatus.APPROVED, amount=_money(40000),
                    counterparty="Supermercado Del Parque",
                    description="Compra con promo 15% cashback miércoles"),
        # C0006: new customer asking for a large refund (must go to human approval).
        Transaction(id=ids.next(), customer_id="C0006", created_at=d - timedelta(days=4),
                    type=TxType.PURCHASE, status=TxStatus.APPROVED, amount=_money(480000),
                    counterparty="Electrodomésticos Norte", description="Heladera"),
    ]
    return customers, tx


def _random_customers(rng: random.Random, ids: _Ids, n: int) -> tuple[list[Customer], list[Transaction]]:
    customers, txs = [], []
    for i in range(7, 7 + n):
        cid = f"C{i:04d}"
        customers.append(Customer(
            id=cid,
            full_name=f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}",
            dni=str(rng.randint(20_000_000, 45_000_000)),
            segment=rng.choice(["standard", "standard", "premium"]),
            created_at=REFERENCE_DATE - timedelta(days=rng.randint(30, 2000)),
        ))
        for _ in range(rng.randint(8, 25)):
            tx_type = rng.choices(
                [TxType.PURCHASE, TxType.QR_PAYMENT, TxType.TRANSFER_OUT,
                 TxType.TRANSFER_IN, TxType.CASHBACK],
                weights=[45, 20, 15, 15, 5],
            )[0]
            amount = {
                TxType.PURCHASE: rng.uniform(1500, 120000),
                TxType.QR_PAYMENT: rng.uniform(500, 25000),
                TxType.TRANSFER_OUT: rng.uniform(5000, 300000),
                TxType.TRANSFER_IN: rng.uniform(5000, 300000),
                TxType.CASHBACK: rng.uniform(100, 3000),
            }[tx_type]
            txs.append(Transaction(
                id=ids.next(), customer_id=cid,
                created_at=REFERENCE_DATE - timedelta(minutes=rng.randint(60, 60 * 24 * 90)),
                type=tx_type,
                status=rng.choices([TxStatus.APPROVED, TxStatus.REJECTED], weights=[95, 5])[0],
                amount=_money(amount),
                counterparty=rng.choice(MERCHANTS) if tx_type in (TxType.PURCHASE, TxType.QR_PAYMENT)
                else f"CBU ****{rng.randint(1000, 9999)}",
            ))
    return customers, txs


def seed(session: Session, n_random: int = 40) -> None:
    """Create the tables and load the data. Idempotent: does nothing if customers already exist."""
    Base.metadata.create_all(session.get_bind())
    if session.query(Customer).first():
        return
    rng = random.Random(42)
    ids = _Ids()
    c1, t1 = _scenario_customers(ids)
    c2, t2 = _random_customers(rng, ids, n_random)
    session.add_all(c1 + c2)
    session.add_all(t1 + t2)
    session.commit()
