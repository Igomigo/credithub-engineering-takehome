"""FastAPI app entry point."""

from fastapi import FastAPI

from . import models  # noqa: F401 — register models on Base
from .audit_api import router as audit_router
from .db import Base, engine
from .loans import router as loans_router
from .payments import router as payments_router

Base.metadata.create_all(bind=engine)

app = FastAPI(title="CreditHub take-home — loan servicing slice")
app.include_router(loans_router)
app.include_router(payments_router)
app.include_router(audit_router)


@app.get("/health")
def health():
    return {"status": "ok"}
