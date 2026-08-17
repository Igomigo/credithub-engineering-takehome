"""Seed synthetic loans + a little payment history.

Run once before starting the app:  python -m app.seed

The seeded events are already reconciled (applied/rejected) — they're history,
so the feed isn't empty on first load. New payments arrive via the webhook (the
"Simulate incoming payment" button).
"""

from .db import Base, SessionLocal, engine
from .models import Loan, LoanStatus, PaymentEvent, PaymentStatus
from .payments import RejectionReason


def seed() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.query(PaymentEvent).delete()
    db.query(Loan).delete()

    loans = [
        Loan(id=1, borrower_name="Adaeze Okafor", principal=100000, total_repayable=112000, total_paid=0, status=LoanStatus.active),
        Loan(id=2, borrower_name="Bola Adeyemi", principal=50000, total_repayable=56000, total_paid=28000, status=LoanStatus.active),
        Loan(id=3, borrower_name="Chidi Nwosu", principal=200000, total_repayable=224000, total_paid=224000, status=LoanStatus.paid_off),
        Loan(id=4, borrower_name="Fatima Bello", principal=75000, total_repayable=84000, total_paid=0, status=LoanStatus.cancelled),
        Loan(id=5, borrower_name="Emeka Obi", principal=300000, total_repayable=339000, total_paid=100000, status=LoanStatus.written_off),
        Loan(id=6, borrower_name="Ngozi Eze", principal=33333, total_repayable=37333.33, total_paid=0, status=LoanStatus.active),
    ]
    db.add_all(loans)

    # History — already reconciled (consistent with the loan balances above).
    events = [
        PaymentEvent(external_ref="PSK-8001", loan_id=2, amount=28000, channel="paystack", status=PaymentStatus.applied),
        PaymentEvent(external_ref="PSK-8002", loan_id=3, amount=224000, channel="gsi", status=PaymentStatus.applied),
        # Reason codes must match RejectionReason in payments.py — the admin
        # panel groups on them, and free text would land in "unclassified".
        PaymentEvent(external_ref="PSK-8003", loan_id=4, amount=5000, channel="cbs",
                     status=PaymentStatus.rejected, reason=RejectionReason.LOAN_NOT_ACTIVE),
    ]
    db.add_all(events)
    db.commit()
    print(f"seeded {len(loans)} loans and {len(events)} historical payment events")
    db.close()


if __name__ == "__main__":
    seed()
