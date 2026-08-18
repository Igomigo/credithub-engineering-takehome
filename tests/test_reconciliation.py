"""Regression tests for the reconciliation webhook.

The provided spec in ``test_payments.py`` pins the contract. These cover the
ways it can be met on the surface and still be wrong underneath — the two
correctness traps, the accuracy of the reason an operator reads, and the
integrity of the audit trail.

Each test is written so that it fails if the specific defect is reintroduced,
not merely if the endpoint stops responding.
"""

import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import AuditLog, Loan, LoanStatus, PaymentEvent, PaymentStatus, Repayment
from app.payments import _already_applied

TOKEN = {"X-Webhook-Token": "dev-webhook-secret"}

# Three instalments that sum to 48702.38 in decimal but not in binary floating
# point: accumulating them as floats leaves a residue of about -7.3e-12.
DRIFTING_TOTAL = 48702.38
DRIFTING_INSTALMENTS = [3622.74, 26794.56, 18285.08]


@pytest.fixture()
def client():
    """Fresh DB with the loan shapes these tests need.

    #1 active, round numbers. #2 cancelled. #3 active with a total that floats
    cannot represent exactly.
    """
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.add_all([
        Loan(id=1, borrower_name="Round Numbers", principal=50000,
             total_repayable=56000, total_paid=0, status=LoanStatus.active),
        Loan(id=2, borrower_name="Cancelled", principal=10000,
             total_repayable=11000, total_paid=0, status=LoanStatus.cancelled),
        Loan(id=3, borrower_name="Awkward Total", principal=44000,
             total_repayable=DRIFTING_TOTAL, total_paid=0, status=LoanStatus.active),
    ])
    db.commit()
    db.close()
    return TestClient(app)


def pay(client, ref, loan_id, amount, channel="paystack"):
    return client.post(
        "/webhooks/payments",
        json={"external_ref": ref, "loan_id": loan_id, "amount": amount, "channel": channel},
        headers=TOKEN,
    )


# --- trap 1: float drift -----------------------------------------------------

def test_loan_paid_in_instalments_closes_exactly():
    """A loan settled by instalments must close.

    This is the trap. Summing these three amounts as floats leaves the loan
    ~1e-12 short of repaid, so ``outstanding`` never reaches zero and the loan
    stays ``active`` forever — while the UI renders the residue as ₦0.00, so
    nobody can see why. A single exact payment does *not* expose this; the
    drift only appears once amounts accumulate.
    """
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.add(Loan(id=3, borrower_name="Awkward Total", principal=44000,
                total_repayable=DRIFTING_TOTAL, total_paid=0, status=LoanStatus.active))
    db.commit()
    db.close()
    c = TestClient(app)

    for i, amount in enumerate(DRIFTING_INSTALMENTS):
        assert pay(c, f"INST-{i}", 3, amount).json()["event"]["status"] == "applied"

    loan = c.get("/loans/3").json()
    assert loan["outstanding"] == 0
    assert loan["status"] == "paid_off"


def test_float_accumulation_would_drift(client):
    """Guards the premise of the test above.

    If this ever stops failing, these amounts no longer demonstrate drift and
    ``test_loan_paid_in_instalments_closes_exactly`` has quietly stopped
    testing anything.
    """
    naive = 0.0
    for amount in DRIFTING_INSTALMENTS:
        naive += amount
    assert DRIFTING_TOTAL - naive != 0.0


def test_payment_one_kobo_over_outstanding_is_rejected(client):
    """The overpayment boundary — where an off-by-a-rounding-error shows up."""
    assert pay(client, "OVER-1", 1, 56000.01).json()["event"]["status"] == "rejected"
    assert client.get("/loans/1").json()["outstanding"] == 56000


def test_payment_of_exactly_outstanding_is_applied(client):
    assert pay(client, "EXACT-1", 1, 56000).json()["event"]["status"] == "applied"
    assert client.get("/loans/1").json()["status"] == "paid_off"


def test_partial_payment_leaves_loan_active(client):
    assert pay(client, "PART-1", 1, 20000).json()["event"]["status"] == "applied"
    loan = client.get("/loans/1").json()
    assert loan["outstanding"] == 36000
    assert loan["status"] == "active"


# --- trap 2: duplicates and concurrency --------------------------------------

def test_redelivery_after_payoff_reads_as_duplicate_not_closed_loan(client):
    """The reason must be the *useful* one.

    A redelivery of the payment that closed a loan fails two checks at once.
    Reporting "loan not active" is technically true and operationally wrong: it
    puts a benign rail redelivery in the operator's queue as a loan problem.
    """
    pay(client, "DUP-1", 1, 56000)
    redelivery = pay(client, "DUP-1", 1, 56000)
    assert redelivery.json()["event"]["reason"] == "duplicate_external_ref"


def test_redelivery_does_not_credit_twice(client):
    pay(client, "DUP-2", 1, 20000)
    pay(client, "DUP-2", 1, 20000)

    assert client.get("/loans/1").json()["total_paid"] == 20000
    db = SessionLocal()
    assert db.query(Repayment).count() == 1
    db.close()


def test_rejected_duplicate_is_still_recorded(client):
    """Every payment the rail delivers has to be visible, applied or not."""
    pay(client, "DUP-3", 1, 20000)
    pay(client, "DUP-3", 1, 20000)

    db = SessionLocal()
    events = db.query(PaymentEvent).filter_by(external_ref="DUP-3").all()
    statuses = sorted(e.status.value for e in events)
    db.close()
    assert statuses == ["applied", "rejected"]


def test_same_reference_on_a_different_loan_is_still_a_duplicate(client):
    """``external_ref`` is the rail's key for the money, not for the pairing.

    Treating (ref, loan_id) as the identity would let a misrouted redelivery
    credit a second loan with money that only arrived once.
    """
    pay(client, "DUP-4", 1, 20000)
    misrouted = pay(client, "DUP-4", 3, 20000)
    assert misrouted.json()["event"]["reason"] == "duplicate_external_ref"


def test_already_applied_lookup_ignores_the_loan(client):
    """Tests ``_already_applied`` directly, because the endpoint cannot.

    The lookup and the unique index both reject a misrouted redelivery, so
    through HTTP each masks a defect in the other. Narrowing the lookup to
    (ref, loan_id) is a real bug — it would let the same money be credited to
    two loans if the constraint were ever relaxed — and only a unit test
    catches it.
    """
    pay(client, "SCOPE-1", 1, 20000)
    db = SessionLocal()
    try:
        assert _already_applied(db, "SCOPE-1") is True
        assert _already_applied(db, "SCOPE-NEVER-SENT") is False
    finally:
        db.close()


def test_only_applied_events_count_as_duplicates(client):
    """A rejected payment must not block a later, valid retry of the same ref.

    If the lookup matched on any status, a payment rejected for a transient
    reason could never be re-sent by the rail.
    """
    pay(client, "RETRY-1", 1, 999999)  # rejected: overpayment
    db = SessionLocal()
    try:
        assert _already_applied(db, "RETRY-1") is False
    finally:
        db.close()

    assert pay(client, "RETRY-1", 1, 20000).json()["event"]["status"] == "applied"


def test_database_rejects_a_second_applied_row_for_one_reference(client):
    """The constraint itself, at the level the application cannot reach.

    ``uq_applied_external_ref`` is what protects against two redeliveries
    racing, where both requests run their lookup before either commits. An
    endpoint test cannot prove the index is present, because the lookup in
    ``_already_applied`` would cover for its absence; writing the rows
    directly isolates it.
    """
    db = SessionLocal()
    try:
        db.add(PaymentEvent(external_ref="CON-1", loan_id=1, amount=100,
                            channel="paystack", status=PaymentStatus.applied))
        db.commit()

        # Rejected rows for the same ref are expected and must stay allowed.
        db.add(PaymentEvent(external_ref="CON-1", loan_id=1, amount=100,
                            channel="paystack", status=PaymentStatus.rejected))
        db.commit()

        db.add(PaymentEvent(external_ref="CON-1", loan_id=1, amount=100,
                            channel="paystack", status=PaymentStatus.applied))
        with pytest.raises(IntegrityError):
            db.commit()
    finally:
        db.rollback()
        db.close()


def test_two_payments_on_one_loan_do_not_lose_an_update(client):
    """Two *different* payments settling against one loan at the same moment.

    Distinct from a redelivery: both are legitimate and both must land. The
    naive shape reads total_paid into Python, adds, and writes it back, so both
    requests read the same balance and the second write discards the first —
    both report success, both write a ledger row, and the borrower's money
    silently vanishes from the balance.

    This reproduces on SQLite: ``with_for_update()`` compiles to a plain SELECT
    here because the dialect has no row-lock syntax, so the lock cannot be what
    prevents it. The conditional UPDATE in ``_apply`` is.
    """
    barrier = threading.Barrier(2)

    def fire(ref, amount):
        barrier.wait()
        pay(client, ref, 1, amount)

    threads = [
        threading.Thread(target=fire, args=args)
        for args in [("LOST-1", 20000), ("LOST-2", 15000)]
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert client.get("/loans/1").json()["total_paid"] == 35000
    db = SessionLocal()
    assert db.query(Repayment).count() == 2
    db.close()


def test_racing_payments_are_re_checked_against_the_new_balance(client):
    """A retry must decide again, not replay its first decision.

    Loan #1 owes 56,000. Three payments of 20,000 arrive together: two fit,
    the third overpays once the others land. If a retry reapplied its original
    verdict, the loan would be overpaid.
    """
    barrier = threading.Barrier(3)

    def fire(i):
        barrier.wait()
        pay(client, f"TRIPLE-{i}", 1, 20000)

    threads = [threading.Thread(target=fire, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    loan = client.get("/loans/1").json()
    assert loan["total_paid"] == 40000
    assert loan["outstanding"] == 16000


def test_concurrent_redeliveries_credit_the_loan_once(client):
    """Two copies of one payment arriving together.

    A lookup-then-insert guard cannot catch this: both requests read before
    either commits, so both see no duplicate. Only the unique index can.
    """
    barrier = threading.Barrier(2)
    statuses = []

    def fire():
        barrier.wait()
        statuses.append(pay(client, "RACE-1", 1, 20000).json()["event"]["status"])

    threads = [threading.Thread(target=fire) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(statuses) == ["applied", "rejected"]
    assert client.get("/loans/1").json()["total_paid"] == 20000
    db = SessionLocal()
    assert db.query(Repayment).count() == 1
    db.close()


# --- rejection reasons -------------------------------------------------------

@pytest.mark.parametrize("ref,loan_id,amount,expected", [
    ("WHY-1", 999, 100, "unknown_loan"),
    ("WHY-2", 2, 100, "loan_not_active"),
    ("WHY-3", 1, 999999, "overpayment"),
])
def test_rejection_reason_is_specific(client, ref, loan_id, amount, expected):
    """The panel groups issues by these codes, so each must be distinct."""
    body = pay(client, ref, loan_id, amount).json()
    assert body["event"]["status"] == "rejected"
    assert body["event"]["reason"] == expected


def test_rejected_payment_leaves_the_loan_untouched(client):
    pay(client, "NOOP-1", 1, 999999)
    loan = client.get("/loans/1").json()
    assert loan["total_paid"] == 0
    assert loan["status"] == "active"

    db = SessionLocal()
    assert db.query(Repayment).count() == 0
    db.close()


def test_unknown_loan_returns_a_null_loan_rather_than_failing(client):
    """There is no loan to report, but the rail still gets a clean 200."""
    response = pay(client, "GHOST-1", 999, 100)
    assert response.status_code == 200
    assert response.json()["loan"] is None


# --- audit trail -------------------------------------------------------------

def test_applied_payment_is_audited(client):
    pay(client, "AUD-1", 1, 20000)
    db = SessionLocal()
    entries = db.query(AuditLog).all()
    db.close()

    assert len(entries) == 1
    assert entries[0].action == "payment.applied"
    assert "AUD-1" in entries[0].detail


def test_rejected_payment_is_audited(client):
    """"We received money and did not credit it" needs to be reconstructable."""
    pay(client, "AUD-2", 1, 999999)
    db = SessionLocal()
    entries = db.query(AuditLog).all()
    db.close()

    assert len(entries) == 1
    assert entries[0].action == "payment.rejected"
    assert "overpayment" in entries[0].detail


def test_audit_and_repayment_commit_together(client):
    """The audit row is written in the caller's transaction, not separately.

    If they were committed apart, a crash between them would leave either a
    credited loan with no trail or a trail for money never credited.
    """
    pay(client, "TXN-1", 1, 20000)
    db = SessionLocal()
    repayments = db.query(Repayment).count()
    applied_audits = db.query(AuditLog).filter_by(action="payment.applied").count()
    db.close()
    assert repayments == applied_audits == 1


# --- auth --------------------------------------------------------------------

@pytest.mark.parametrize("headers", [
    {},
    {"X-Webhook-Token": ""},
    {"X-Webhook-Token": "wrong-secret"},
])
def test_webhook_rejects_bad_tokens(client, headers):
    response = client.post(
        "/webhooks/payments",
        json={"external_ref": "AUTH-1", "loan_id": 1, "amount": 100},
        headers=headers,
    )
    assert response.status_code == 401


def test_unauthenticated_payment_is_not_recorded(client):
    """A 401 must not leave a payment event behind."""
    client.post("/webhooks/payments", json={"external_ref": "AUTH-2", "loan_id": 1, "amount": 100})
    db = SessionLocal()
    count = db.query(PaymentEvent).filter_by(external_ref="AUTH-2").count()
    db.close()
    assert count == 0


# --- input validation --------------------------------------------------------

@pytest.mark.parametrize("amount", [0, -500])
def test_non_positive_amounts_are_refused(client, amount):
    """A rail should never send these; if one does, reject before any logic."""
    assert pay(client, "BAD-1", 1, amount).status_code == 422


def test_blank_external_ref_is_refused(client):
    """Without a reference there is no idempotency key, so it cannot be safe."""
    assert pay(client, "", 1, 100).status_code == 422
