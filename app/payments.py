"""Payment ingestion + reconciliation.

``GET /payment-events`` is the feed. ``POST /webhooks/payments`` is where a
rail (gateway/GSI/CBS) hands us a payment: it is recorded, matched to its loan,
and applied or rejected in the same call — there is no separate "apply" step.

The module is arranged so that deciding and doing stay apart:
``_rejection_reason`` is the entire reconciliation policy and touches nothing,
``_apply``/``_reject`` only persist, and the route wires them together inside a
single transaction.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from .audit import record_audit
from .auth import require_webhook_token
from .db import get_db
from .models import Loan, LoanStatus, PaymentEvent, PaymentStatus, Repayment
from .money import to_kobo, to_naira

router = APIRouter()

WEBHOOK_ACTOR = "payment-webhook"


class RejectionReason:
    """Why a payment could not be applied.

    Stable machine-readable codes: they are persisted on the event, and the
    admin panel groups issues by them. Free text would drift between the two
    sides and break the grouping silently.
    """

    UNKNOWN_LOAN = "unknown_loan"
    DUPLICATE = "duplicate_external_ref"
    LOAN_NOT_ACTIVE = "loan_not_active"
    OVERPAYMENT = "overpayment"


class PaymentIn(BaseModel):
    external_ref: str = Field(min_length=1)
    loan_id: int
    amount: float = Field(gt=0)
    channel: str = "paystack"


def _event_out(e: PaymentEvent) -> dict:
    return {
        "id": e.id,
        "external_ref": e.external_ref,
        "loan_id": e.loan_id,
        "amount": e.amount,
        "channel": e.channel,
        "status": e.status.value,
        "reason": e.reason,
        "received_at": e.received_at.isoformat() if e.received_at else None,
        "processed_at": e.processed_at.isoformat() if e.processed_at else None,
    }


@router.get("/payment-events")
def list_payment_events(db=Depends(get_db)):
    """The payments feed (newest first) — provided."""
    events = db.query(PaymentEvent).order_by(PaymentEvent.id.desc()).all()
    return [_event_out(e) for e in events]


def _lock_loan(db, loan_id: int) -> Loan | None:
    """Fetch the loan for update, so a concurrent payment cannot interleave.

    Without the lock, two payments against the same loan both read the same
    ``total_paid``, both compute a new total from that stale figure, and the
    second write silently discards the first — money the borrower paid simply
    disappears.

    ``with_for_update()`` is a no-op on SQLite, which serialises writers at the
    file level anyway; it is what makes this correct on the Postgres the real
    platform runs. See NOTES.md.
    """
    return (
        db.query(Loan)
        .filter(Loan.id == loan_id)
        .with_for_update()
        .one_or_none()
    )


def _already_applied(db, external_ref: str) -> bool:
    """Has this rail reference already been credited?

    This catches the ordinary case — a redelivery arriving some time after the
    original. It cannot catch two redeliveries racing each other, because both
    would run this query before either commits; ``uq_applied_external_ref``
    covers that. The lookup earns its place by producing the *right reason*:
    without it a redelivery that closed its loan would be reported as
    "loan not active", sending an operator after a problem that isn't there.
    """
    return db.query(
        db.query(PaymentEvent)
        .filter(
            PaymentEvent.external_ref == external_ref,
            PaymentEvent.status == PaymentStatus.applied,
        )
        .exists()
    ).scalar()


def _rejection_reason(
    loan: Loan | None, amount_kobo: int, is_duplicate: bool
) -> str | None:
    """Decide whether a payment can be applied. ``None`` means apply it.

    Pure: no database, no side effects. The whole reconciliation policy is
    these four rules, in this order.

    Order matters where a payment fails more than one check, because the
    surviving reason is what an operator reads. A redelivery of the payment
    that closed a loan fails both the duplicate and the not-active check;
    "duplicate" is the true story and the one that needs no action, so it is
    classified first. An unknown loan outranks it only because there is no
    loan to reason about at all.
    """
    if loan is None:
        return RejectionReason.UNKNOWN_LOAN
    if is_duplicate:
        return RejectionReason.DUPLICATE
    if loan.status is not LoanStatus.active:
        return RejectionReason.LOAN_NOT_ACTIVE

    outstanding_kobo = to_kobo(loan.total_repayable) - to_kobo(loan.total_paid)
    if amount_kobo > outstanding_kobo:
        return RejectionReason.OVERPAYMENT
    return None


def _apply(db, event: PaymentEvent, loan: Loan, amount_kobo: int) -> None:
    """Credit the payment to the loan, closing it if that settles the debt.

    Balances are recomputed in kobo, never by adding floats to floats, so a
    part-paid loan still lands exactly on zero. The float columns are written
    once, at the end, as storage.
    """
    total_paid_kobo = to_kobo(loan.total_paid) + amount_kobo
    loan.total_paid = to_naira(total_paid_kobo)
    if total_paid_kobo >= to_kobo(loan.total_repayable):
        loan.status = LoanStatus.paid_off

    db.add(Repayment(loan_id=loan.id, payment_event_id=event.id, amount=event.amount))
    event.status = PaymentStatus.applied
    event.processed_at = datetime.now(timezone.utc)

    record_audit(
        db,
        action="payment.applied",
        entity="loan",
        entity_id=loan.id,
        actor=WEBHOOK_ACTOR,
        detail=(
            f"{event.external_ref} via {event.channel}: applied "
            f"{event.amount:.2f}; loan now {loan.status.value}"
        ),
    )


def _reject(db, event: PaymentEvent, reason: str) -> None:
    """Record the payment as unapplied, with why.

    Rejections are audited too: "we received money and did not credit it" is
    exactly the kind of decision someone will need to reconstruct later.
    """
    event.status = PaymentStatus.rejected
    event.reason = reason
    event.processed_at = datetime.now(timezone.utc)

    record_audit(
        db,
        action="payment.rejected",
        entity="loan",
        entity_id=event.loan_id,
        actor=WEBHOOK_ACTOR,
        detail=f"{event.external_ref} via {event.channel}: {reason}",
    )


@router.post("/webhooks/payments")
def receive_payment(
    body: PaymentIn,
    db=Depends(get_db),
    _token: str = Depends(require_webhook_token),
):
    """Reconcile an incoming payment on receipt.

    Every payment is recorded, whether or not it can be applied — the rail
    delivered it, and an operator needs to see it. Rejections are therefore
    ``200`` with a reason, not an error status: a 4xx/5xx would tell the rail
    we failed to receive it and it would redeliver forever.

    Recording the event, crediting the loan, and writing the audit row all
    commit together. Committing the event first — "so we have a record no
    matter what" — is the tempting shape and it is wrong: a crash between the
    two commits leaves a payment recorded but never applied, and the books no
    longer balance.
    """
    amount_kobo = to_kobo(body.amount)
    loan = _lock_loan(db, body.loan_id)

    event = PaymentEvent(
        external_ref=body.external_ref,
        loan_id=body.loan_id,
        amount=body.amount,
        channel=body.channel,
    )
    db.add(event)
    db.flush()  # assign event.id for the repayment's FK

    reason = _rejection_reason(loan, amount_kobo, _already_applied(db, body.external_ref))
    if reason is None:
        _apply(db, event, loan, amount_kobo)
    else:
        _reject(db, event, reason)

    try:
        db.commit()
    except IntegrityError:
        # uq_applied_external_ref fired: this ref was already applied, either
        # earlier or by a request that raced us. Record the redelivery as a
        # rejected duplicate in its own transaction.
        db.rollback()
        event = _record_duplicate(db, body)

    db.refresh(event)
    return {"event": _event_out(event), "loan": _loan_snapshot(db, body.loan_id)}


def _record_duplicate(db, body: PaymentIn) -> PaymentEvent:
    """Persist a redelivery that lost the race to the unique index.

    Runs in a fresh transaction: the one that hit the constraint was rolled
    back, so the event row it held is gone and has to be written again.
    """
    event = PaymentEvent(
        external_ref=body.external_ref,
        loan_id=body.loan_id,
        amount=body.amount,
        channel=body.channel,
    )
    db.add(event)
    _reject(db, event, RejectionReason.DUPLICATE)
    db.commit()
    return event


def _loan_snapshot(db, loan_id: int) -> dict | None:
    """The loan as it stands after reconciling — ``None`` if the ref was bogus."""
    loan = db.get(Loan, loan_id)
    if loan is None:
        return None
    return {
        "id": loan.id,
        "borrower_name": loan.borrower_name,
        "total_repayable": loan.total_repayable,
        "total_paid": loan.total_paid,
        "outstanding": loan.outstanding,
        "status": loan.status.value,
    }
