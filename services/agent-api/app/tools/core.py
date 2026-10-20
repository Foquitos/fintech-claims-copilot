"""Read tools against core-mock (RF-003).

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

    def _get(self, path: str, params: dict | None = None):
        try:
            r = self._http.get(path, params=params)
        except httpx.HTTPError as e:
            raise CoreError(f"GET {path}: {e}") from e
        if r.status_code == 404:
            raise NotFound(f"GET {path}: not found")
        if r.is_error:
            raise CoreError(f"GET {path}: HTTP {r.status_code}")
        return r.json()
