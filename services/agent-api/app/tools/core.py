"""Tools against core-mock: reads (RF-003) and the refund write (RF-007).

The graph depends on this small client, not on httpx, so tests inject a mock transport and
need no running service. Failures surface as two exceptions the graph turns into actions:
NotFound -> ask the customer for data (AC-003.2), CoreError -> escalate (PRD 9.1.6).
"""
import os

import httpx

TIMEOUT_S = 5  # PRD 9.1.6: core-mock slow or down -> the tool fails and the case escalates
LOOKBACK_DAYS = 30


class CoreError(Exception):
    """core-mock is down, timed out or answered with an unexpected error."""


class NotFound(CoreError):
    """The customer or transaction does not exist."""


class CoreClient:
    def __init__(self, base_url: str | None = None,
                 transport: httpx.BaseTransport | None = None) -> None:
        self._http = httpx.Client(base_url=base_url or os.getenv("CORE_URL", "http://localhost:8001"),
                                  timeout=TIMEOUT_S, transport=transport)

    def get_customer(self, customer_id: str) -> dict:
        return self._get(f"/customers/{customer_id}")

    def list_transactions(self, customer_id: str, days: int = LOOKBACK_DAYS) -> list[dict]:
        return self._get(f"/customers/{customer_id}/transactions", params={"days": days})

    def get_transaction(self, transaction_id: str) -> dict:
        return self._get(f"/transactions/{transaction_id}")

    def today(self) -> str:
        """The core's clock (ISO date). core-mock's data is anchored to a fixed date."""
        return self._get("/health")["reference_date"][:10]

    def create_refund(self, transaction_id: str, amount: float, reason: str) -> dict:
        # core-mock decides the status: above its limits the refund stays pending_approval.
        return self._request("POST", "/refunds", json={
            "transaction_id": transaction_id, "amount": str(amount), "reason": reason})

    def approve_refund(self, refund_id: str) -> dict:
        return self._request("POST", f"/refunds/{refund_id}/approve")

    def _get(self, path: str, params: dict | None = None):
        return self._request("GET", path, params=params)

    def _request(self, method: str, path: str, **kwargs):
        # Writes are never retried here (PRD 9.1.6): a failure goes back to the graph.
        try:
            r = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise CoreError(f"{method} {path}: {e}") from e
        if r.status_code == 404:
            raise NotFound(f"{method} {path}: not found")
        if r.is_error:
            raise CoreError(f"{method} {path}: HTTP {r.status_code}")
        return r.json()
