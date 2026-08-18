"""Webhook authentication.

Two mechanisms, checked in order of strength:

1. ``X-Webhook-Signature`` — an HMAC over the timestamp and the raw body, the
   way Paystack, Stripe and Flutterwave actually sign their callbacks.
2. ``X-Webhook-Token`` — the shared token this exercise ships with.

A shared token only proves the caller once saw the secret. It says nothing
about the message: anyone who captures a valid request can replay it forever,
and a proxy could change ``amount`` from 20,000 to 2,000,000 and the token
would still match. A signature covers the body, so altering a single character
invalidates it, and covering a timestamp bounds how long a captured request
stays useful.

The token path is kept so existing callers (and the provided test spec) keep
working — a real rollout runs both while providers migrate, then drops the
token. See NOTES.md.
"""

import hashlib
import hmac
import os
import time

from fastapi import HTTPException, Request

# Both secrets come from the environment, falling back to the exercise's dev
# values so the repo still runs with no setup. A real deployment would refuse
# to boot without them rather than fall back to a published default.
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "dev-webhook-secret")
WEBHOOK_SIGNING_SECRET = os.getenv("WEBHOOK_SIGNING_SECRET", "dev-signing-secret")

# How old a signed request may be. Long enough to survive a slow network and
# modest clock skew, short enough that a captured request stops being useful.
SIGNATURE_TOLERANCE_SECONDS = 300


def sign(timestamp: int, body: bytes, secret: str = WEBHOOK_SIGNING_SECRET) -> str:
    """Build the signature for a request. Shared with the tests and any sender.

    The timestamp is inside the signed material, not merely alongside it —
    otherwise an attacker could replay a captured body with a fresh timestamp.
    """
    payload = f"{timestamp}.".encode() + body
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def _parse_signature_header(header: str) -> tuple[int, str] | None:
    """Read ``t=<unix seconds>,v1=<hex digest>``.

    The scheme is versioned so the digest can be changed later without
    breaking senders still using the old one.
    """
    parts = dict(
        piece.split("=", 1) for piece in header.split(",") if "=" in piece
    )
    try:
        return int(parts["t"]), parts["v1"]
    except (KeyError, ValueError):
        return None


async def require_webhook_auth(request: Request) -> str:
    """Authenticate an incoming webhook. Raises 401 if it cannot be trusted.

    Reads the raw body rather than a parsed model: the signature covers the
    exact bytes the sender hashed, and re-serialising parsed JSON would produce
    different bytes (key order, whitespace) and never match.
    """
    signature_header = request.headers.get("x-webhook-signature")
    if signature_header:
        return _verify_signature(signature_header, await request.body())

    token = request.headers.get("x-webhook-token", "")
    # Constant-time even here: a plain != leaks how many leading characters
    # were correct, which is enough to recover a secret one byte at a time.
    if not hmac.compare_digest(token, WEBHOOK_TOKEN):
        raise HTTPException(status_code=401, detail="invalid or missing webhook token")
    return "token"


def _verify_signature(header: str, body: bytes) -> str:
    parsed = _parse_signature_header(header)
    if parsed is None:
        raise HTTPException(status_code=401, detail="malformed webhook signature")

    timestamp, provided = parsed
    if abs(time.time() - timestamp) > SIGNATURE_TOLERANCE_SECONDS:
        # Covers both a replayed old request and one signed with a future
        # timestamp to sidestep the window.
        raise HTTPException(status_code=401, detail="webhook signature expired")

    if not hmac.compare_digest(sign(timestamp, body), provided):
        raise HTTPException(status_code=401, detail="invalid webhook signature")
    return "signature"
