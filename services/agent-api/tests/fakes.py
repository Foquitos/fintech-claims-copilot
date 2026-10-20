"""Test doubles shared by the graph and API tests: a scripted LLM and an in-memory core-mock."""
import json

import httpx

from app.agent.state import Action, Category, Classification, ProposalDraft
from app.tools.core import CoreClient

TODAY = "2026-10-10"
CUSTOMERS = {
    "C0001": {"id": "C0001", "full_name": "Lucía Fernández", "dni_masked": "*****222",
              "segment": "standard", "created_at": "2024-04-10T12:00:00", "card_blocked": False},
    # 60 days old: refunds above $20,000 need a supervisor.
    "C0006": {"id": "C0006", "full_name": "Tomás Acosta", "dni_masked": "*****333",
              "segment": "standard", "created_at": "2026-08-11T12:00:00", "card_blocked": False},
}
TRANSACTIONS = {
    "C0001": [
        {"id": "T0002", "customer_id": "C0001", "created_at": "2026-09-28T15:30:00",
         "type": "card_purchase", "status": "approved", "amount": "45999.00",
         "counterparty": "Zapatillas Run", "description": "Compra con tarjeta"},
        {"id": "T0001", "customer_id": "C0001", "created_at": "2026-09-28T15:28:00",
         "type": "card_purchase", "status": "approved", "amount": "45999.00",
         "counterparty": "Zapatillas Run", "description": "Compra con tarjeta"},
    ],
    "C0006": [
        {"id": "T0008", "customer_id": "C0006", "created_at": "2026-10-06T12:00:00",
         "type": "purchase", "status": "approved", "amount": "480000.00",
         "counterparty": "Electrodomésticos Norte", "description": "Heladera"},
        {"id": "T0007", "customer_id": "C0006", "created_at": "2026-10-06T11:57:00",
         "type": "purchase", "status": "approved", "amount": "480000.00",
         "counterparty": "Electrodomésticos Norte", "description": "Heladera"},
    ],
}

CLAIM = "Me cobraron dos veces las zapatillas"
DUP = Classification(category=Category.DUPLICATE_CHARGE, confidence=0.93)


def draft(cited_policies, action=Action.REFUND, amount=45999.0, transaction_id="T0002",
          confidence=0.9, reply="Te reintegramos el cobro T0002.") -> ProposalDraft:
    return ProposalDraft(action=action, amount=amount, transaction_id=transaction_id,
                         cited_policies=cited_policies, customer_reply=reply,
                         confidence=confidence)


class FakeLLM:
    """Returns queued replies in order (an Exception instance is raised) and records calls."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[dict] = []

    def generate(self, system, user, schema):
        self.calls.append({"system": system, "user": user, "schema": schema})
        assert self.replies, "unexpected LLM call"
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeCore:
    """A real CoreClient over a mock transport that serves CUSTOMERS/TRANSACTIONS.

    Refunds follow core-mock's standard limit ($50,000). `hold_refunds` makes it hold every
    refund, to test core-mock being stricter than the graph; `writes_down` fails only writes;
    `already_refunded` answers 409 like core-mock does for a second refund on a transaction.
    """

    def __init__(self, down: bool = False, hold_refunds: bool = False, writes_down: bool = False,
                 already_refunded: bool = False):
        self.requests: list[str] = []
        self.down, self.hold_refunds, self.writes_down = down, hold_refunds, writes_down
        self.already_refunded = already_refunded
        self.client = CoreClient("http://core", transport=httpx.MockTransport(self._handle))

    @property
    def writes(self) -> list[str]:
        return [r for r in self.requests if r.startswith("POST")]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(f"{request.method} {request.url.path}")
        if self.down or (self.writes_down and request.method == "POST"):
            raise httpx.ConnectTimeout("core-mock timed out", request=request)
        parts = request.url.path.strip("/").split("/")
        if parts == ["health"]:
            return httpx.Response(200, json={"status": "ok", "reference_date": f"{TODAY}T12:00:00"})
        if parts == ["refunds"] and self.already_refunded:
            return httpx.Response(409, json={"detail": "Transaction already has refund R0"})
        if parts == ["refunds"]:
            data = json.loads(request.content)
            held = self.hold_refunds or float(data["amount"]) > 50000
            return httpx.Response(201, json={
                "id": "R1", "transaction_id": data["transaction_id"], "amount": data["amount"],
                "status": "pending_approval" if held else "approved"})
        if parts[0] == "refunds" and parts[-1] == "approve":
            return httpx.Response(200, json={"id": parts[1], "transaction_id": "?",
                                             "amount": "0", "status": "approved"})
        if parts[0] == "customers" and parts[1] in CUSTOMERS:
            if len(parts) == 2:
                return httpx.Response(200, json=CUSTOMERS[parts[1]])
            return httpx.Response(200, json=TRANSACTIONS[parts[1]])
        return httpx.Response(404, json={"detail": "Customer not found"})
