"""Tests for webhook authentication.

The signature path is what a real rail uses; the token path is what this
exercise ships with and what the provided spec exercises. Both must work, and
neither may be bypassable.
"""

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.auth import SIGNATURE_TOLERANCE_SECONDS, sign
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Loan, LoanStatus, PaymentEvent


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


def signed_post(client, payload, *, timestamp=None, secret=None, body=None):
    """POST with a real signature, computed the way a rail would."""
    raw = body if body is not None else json.dumps(payload).encode()
    ts = int(time.time()) if timestamp is None else timestamp
    signature = sign(ts, raw) if secret is None else sign(ts, raw, secret)
    return client.post(
        "/webhooks/payments",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Signature": f"t={ts},v1={signature}",
        },
    )


PAYMENT = {"external_ref": "SIG-1", "loan_id": 1, "amount": 20000, "channel": "paystack"}


def test_valid_signature_is_accepted(client):
    response = signed_post(client, PAYMENT)
    assert response.status_code == 200
    assert response.json()["event"]["status"] == "applied"


def test_signature_signed_with_the_wrong_secret_is_rejected(client):
    assert signed_post(client, PAYMENT, secret="not-the-secret").status_code == 401


def test_tampered_body_is_rejected(client):
    """The point of signing: altering the amount must invalidate the signature.

    A shared token cannot do this — anyone relaying the request could raise the
    amount and the token would still match.
    """
    raw = json.dumps(PAYMENT).encode()
    ts = int(time.time())
    signature = sign(ts, raw)

    tampered = json.dumps({**PAYMENT, "amount": 2_000_000}).encode()
    response = client.post(
        "/webhooks/payments",
        content=tampered,
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Signature": f"t={ts},v1={signature}",
        },
    )
    assert response.status_code == 401


def test_replayed_old_request_is_rejected(client):
    """A signature alone never expires; the timestamp is what bounds it."""
    stale = int(time.time()) - SIGNATURE_TOLERANCE_SECONDS - 60
    assert signed_post(client, PAYMENT, timestamp=stale).status_code == 401


def test_future_timestamp_is_rejected(client):
    """Guards the other side of the window, not just the past."""
    ahead = int(time.time()) + SIGNATURE_TOLERANCE_SECONDS + 60
    assert signed_post(client, PAYMENT, timestamp=ahead).status_code == 401


def test_timestamp_cannot_be_swapped_for_a_fresh_one(client):
    """The timestamp is inside the signed material, not merely beside it.

    If it were only a header, a captured body could be replayed indefinitely by
    attaching the current time.
    """
    raw = json.dumps(PAYMENT).encode()
    old = int(time.time()) - 3600
    signature = sign(old, raw)          # signed an hour ago

    response = client.post(
        "/webhooks/payments",
        content=raw,
        headers={
            "Content-Type": "application/json",
            # attacker swaps in a current timestamp to beat the window
            "X-Webhook-Signature": f"t={int(time.time())},v1={signature}",
        },
    )
    assert response.status_code == 401


@pytest.mark.parametrize("header", [
    "",
    "garbage",
    "t=abc,v1=deadbeef",       # unparseable timestamp
    "v1=deadbeef",             # no timestamp
    "t=1700000000",            # no digest
])
def test_malformed_signature_headers_are_rejected(client, header):
    response = client.post(
        "/webhooks/payments",
        json=PAYMENT,
        headers={"X-Webhook-Signature": header} if header else {},
    )
    assert response.status_code == 401


def test_a_rejected_request_records_nothing(client):
    """Authentication runs before any write, so a forgery leaves no trace."""
    signed_post(client, PAYMENT, secret="not-the-secret")
    db = SessionLocal()
    count = db.query(PaymentEvent).count()
    db.close()
    assert count == 0


def test_token_still_works_alongside_signatures(client):
    """The migration path: existing senders keep working while rails move over."""
    response = client.post(
        "/webhooks/payments",
        json=PAYMENT,
        headers={"X-Webhook-Token": "dev-webhook-secret"},
    )
    assert response.status_code == 200
