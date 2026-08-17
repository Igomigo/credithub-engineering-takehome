"""Read endpoint for the audit trail.

Kept apart from ``loans.py`` (loan reads) and ``payments.py`` (ingestion): the
audit log is a cross-cutting record of decisions, not a view of either entity.

The trail answers "why does this loan look like this?" — which is the question
an operator asks after the fact, and the one a lender has to be able to answer
under scrutiny.
"""

from fastapi import APIRouter, Depends, Query

from .db import get_db
from .models import AuditLog

router = APIRouter()

DEFAULT_LIMIT = 100
MAX_LIMIT = 500


def _entry_out(entry: AuditLog) -> dict:
    return {
        "id": entry.id,
        "action": entry.action,
        "entity": entry.entity,
        "entity_id": entry.entity_id,
        "actor": entry.actor,
        "detail": entry.detail,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


@router.get("/audit-log")
def list_audit_log(
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    db=Depends(get_db),
):
    """Recent audit entries, newest first.

    Bounded by default: the trail only grows, and an operator dashboard should
    never be one unlucky query away from pulling a year of history.
    """
    entries = (
        db.query(AuditLog)
        .order_by(AuditLog.id.desc())
        .limit(limit)
        .all()
    )
    return [_entry_out(entry) for entry in entries]
