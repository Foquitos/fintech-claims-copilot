from fastapi.testclient import TestClient

from app.main import app


def client() -> TestClient:
    return TestClient(app)


def test_health():
    with client() as c:
        assert c.get("/health").json()["status"] == "ok"


def test_customer_dni_is_masked():
    with client() as c:
        body = c.get("/customers/C0001").json()
        assert body["dni_masked"] == "*****222"
        assert "dni" not in body


def test_unknown_customer_404():
    with client() as c:
        assert c.get("/customers/C9999").status_code == 404


def test_duplicate_charge_scenario():
    with client() as c:
        txs = c.get("/customers/C0001/transactions", params={"days": 7, "type": "purchase"}).json()
        same = [t for t in txs if t["counterparty"] == "Tienda Deportiva Gol"]
        assert len(same) == 2
        assert same[0]["amount"] == same[1]["amount"]


def test_pending_transfer_scenario():
    with client() as c:
        txs = c.get("/customers/C0002/transactions", params={"status": "pending"}).json()
        assert len(txs) == 1 and txs[0]["type"] == "transfer_out"


def test_refund_small_is_auto_approved():
    with client() as c:
        tx = c.get("/customers/C0001/transactions").json()[0]
        r = c.post("/refunds", json={"transaction_id": tx["id"], "amount": "18500",
                                     "reason": "Duplicate charge"})
        assert r.status_code == 201 and r.json()["status"] == "approved"


def test_refund_large_needs_human_approval():
    with client() as c:
        tx = c.get("/customers/C0006/transactions").json()[0]
        r = c.post("/refunds", json={"transaction_id": tx["id"], "amount": "480000",
                                     "reason": "Product not delivered"})
        assert r.json()["status"] == "pending_approval"


def test_refund_cannot_exceed_transaction():
    with client() as c:
        tx = c.get("/customers/C0004/transactions").json()[0]
        r = c.post("/refunds", json={"transaction_id": tx["id"], "amount": "999999",
                                     "reason": "Limit check"})
        assert r.status_code == 422
