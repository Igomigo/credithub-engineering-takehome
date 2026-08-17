"""Tests for the audit trail endpoint."""

import pytest
from fastapi.testclient import TestClient

from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Loan, LoanStatus

TOKEN = {"X-Webhook-Token": "dev-webhook-secret"}


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.add(Loan(id=1, borrower_name="Test One", principal=50000,
                total_repayable=56000, total_paid=0, status=LoanStatus.active))
    db.commit()
    db.close()
    return TestClient(app)


def pay(client, ref, amount, loan_id=1):
    return client.post(
        "/webhooks/payments",
        json={"external_ref": ref, "loan_id": loan_id, "amount": amount},
        headers=TOKEN,
    )


def test_empty_trail_is_an_empty_list(client):
    assert client.get("/audit-log").json() == []


def test_trail_records_both_outcomes(client):
    pay(client, "A-1", 20000)      # applied
    pay(client, "A-2", 999999)     # rejected: overpayment

    actions = [e["action"] for e in client.get("/audit-log").json()]
    assert sorted(actions) == ["payment.applied", "payment.rejected"]


def test_trail_is_newest_first(client):
    """An operator reads the top of this list, so ordering is not cosmetic."""
    pay(client, "ORD-1", 1000)
    pay(client, "ORD-2", 2000)

    entries = client.get("/audit-log").json()
    assert "ORD-2" in entries[0]["detail"]
    assert "ORD-1" in entries[1]["detail"]


def test_limit_caps_the_response(client):
    for i in range(5):
        pay(client, f"LIM-{i}", 100)
    assert len(client.get("/audit-log?limit=3").json()) == 3


@pytest.mark.parametrize("limit", [0, -1, 501])
def test_out_of_range_limits_are_refused(client, limit):
    """The trail only grows; an unbounded query is a footgun worth closing."""
    assert client.get(f"/audit-log?limit={limit}").status_code == 422


def test_entry_carries_what_an_operator_needs(client):
    pay(client, "DET-1", 20000)
    entry = client.get("/audit-log").json()[0]

    assert entry["entity"] == "loan"
    assert entry["entity_id"] == "1"
    assert entry["actor"] == "payment-webhook"
    assert "DET-1" in entry["detail"]
    assert entry["created_at"] is not None
