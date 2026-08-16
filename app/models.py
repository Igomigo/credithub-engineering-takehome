"""ORM models for the loan-servicing slice.

Kept intentionally close to the real platform's shape — including the fact
that money is stored as ``Float``. Treat this as the production schema you've
inherited.

Flow: payments arrive as ``PaymentEvent`` rows (simulated gateway callbacks) in
``pending`` status. Reconciling them — matching to a loan, applying the money,
and ticking the event off — is the candidate's task.
"""

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import relationship

from .db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LoanStatus(str, enum.Enum):
    active = "active"
    paid_off = "paid_off"
    cancelled = "cancelled"
    written_off = "written_off"


class PaymentStatus(str, enum.Enum):
    pending = "pending"    # landed, not yet reconciled
    applied = "applied"    # ticked off against a loan
    rejected = "rejected"  # could not be applied (bad loan state, duplicate, …)


class Loan(Base):
    __tablename__ = "loans"

    id = Column(Integer, primary_key=True)
    borrower_name = Column(String, nullable=False)
    # NOTE: NGN amounts are stored as Float here, mirroring the platform's
    # current models. This is deliberate — do not assume it's correct.
    principal = Column(Float, nullable=False)
    total_repayable = Column(Float, nullable=False)  # principal + interest
    total_paid = Column(Float, nullable=False, default=0.0)
    status = Column(Enum(LoanStatus), nullable=False, default=LoanStatus.active)
    disbursed_at = Column(DateTime, default=_utcnow)

    @property
    def outstanding(self) -> float:
        return self.total_repayable - self.total_paid


class PaymentEvent(Base):
    """An incoming payment from a rail (gateway/GSI/CBS). Starts ``pending``.

    ``external_ref`` is the rail's own id for the payment — the idempotency key.
    It is intentionally NOT unique at the DB level: real rails redeliver, so the
    same ``external_ref`` can arrive more than once, and de-duplicating is part
    of the task.

    De-duplication is enforced by ``uq_applied_external_ref`` below rather than
    by an application-level lookup. A ``SELECT`` for an existing ref followed by
    an ``INSERT`` is check-then-act: two redeliveries arriving together both see
    no match and both apply, double-crediting the borrower. The database is the
    only thing that can arbitrate between concurrent writers, so we let it — a
    losing writer gets an ``IntegrityError``, which *is* the duplicate signal.

    The index is *partial* — scoped to ``applied`` rows — because the contract
    requires recording every incoming payment, including the redeliveries we
    reject. A full unique index would block writing those rejection rows.
    """

    __tablename__ = "payment_events"

    id = Column(Integer, primary_key=True)
    external_ref = Column(String, nullable=False)  # rail's payment id (idempotency key)
    loan_id = Column(Integer, ForeignKey("loans.id"), nullable=False)
    amount = Column(Float, nullable=False)
    channel = Column(String, nullable=False, default="paystack")
    status = Column(Enum(PaymentStatus), nullable=False, default=PaymentStatus.pending)
    reason = Column(String, nullable=True)  # why it was rejected, if it was
    received_at = Column(DateTime, default=_utcnow)
    processed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        # At most one *applied* event per external_ref. Rejected duplicates are
        # still recorded, so the constraint cannot cover every row.
        Index(
            "uq_applied_external_ref",
            "external_ref",
            unique=True,
            sqlite_where=(status == PaymentStatus.applied),
            postgresql_where=(status == PaymentStatus.applied),
        ),
    )


class Repayment(Base):
    """Ledger row created when a payment event is applied to a loan."""

    __tablename__ = "repayments"

    id = Column(Integer, primary_key=True)
    loan_id = Column(Integer, ForeignKey("loans.id"), nullable=False)
    payment_event_id = Column(Integer, ForeignKey("payment_events.id"), nullable=True)
    amount = Column(Float, nullable=False)
    created_at = Column(DateTime, default=_utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True)
    action = Column(String, nullable=False)
    entity = Column(String, nullable=False)
    entity_id = Column(String, nullable=False)
    actor = Column(String, nullable=False)
    detail = Column(String, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
