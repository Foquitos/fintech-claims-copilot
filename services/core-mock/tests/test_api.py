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


def test_new_account_has_a_lower_limit():
    # C0006 is 60 days old: $20,000 or less is automatic, above it waits for a supervisor.
    with client() as c:
        tx1, tx2 = c.get("/customers/C0006/transactions").json()
        ok = c.post("/refunds", json={"transaction_id": tx1["id"], "amount": "20000",
                                      "reason": "Limit check"})
        held = c.post("/refunds", json={"transaction_id": tx2["id"], "amount": "20001",
                                        "reason": "Limit check"})
        assert ok.json()["status"] == "approved"
        assert held.json()["status"] == "pending_approval"


def test_old_account_keeps_the_standard_limit():
    # AC-006.2: $18,500 on a 900-day-old account needs no approval; $50,000 neither.
    with client() as c:
        tx = c.get("/customers/C0001/transactions").json()[0]
        r = c.post("/refunds", json={"transaction_id": tx["id"], "amount": "18500",
                                     "reason": "Limit check"})
        assert r.json()["status"] == "approved"


def test_pending_refund_can_be_approved_once():
    with client() as c:
        tx = c.get("/customers/C0006/transactions").json()[0]
        r = c.post("/refunds", json={"transaction_id": tx["id"], "amount": "480000",
                                     "reason": "Duplicate charge"}).json()
        approved = c.post(f"/refunds/{r['id']}/approve")
        assert approved.status_code == 200 and approved.json()["status"] == "approved"
        assert c.post(f"/refunds/{r['id']}/approve").status_code == 409


def test_approve_unknown_refund_404():
    with client() as c:
        assert c.post("/refunds/R404/approve").status_code == 404


def test_second_refund_on_the_same_transaction_is_409():
    # A claim sent twice must not pay twice, whatever the first refund's status.
    with client() as c:
        tx = c.get("/customers/C0001/transactions").json()[0]
        body = {"transaction_id": tx["id"], "amount": "18500", "reason": "Duplicate charge"}
        first = c.post("/refunds", json=body)
        again = c.post("/refunds", json={**body, "amount": "1"})
        assert first.status_code == 201
        assert again.status_code == 409 and first.json()["id"] in again.json()["detail"]
