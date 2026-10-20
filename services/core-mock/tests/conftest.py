import pytest
from sqlalchemy import delete

from app.db import SessionLocal
from app.models import RefundRequest


@pytest.fixture(autouse=True)
def no_refunds():
    """Tests share one in-memory database; each starts without refunds (one per transaction)."""
    yield
    with SessionLocal() as s:
        s.execute(delete(RefundRequest))
        s.commit()
