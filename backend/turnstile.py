"""Cloudflare Turnstile verification for the public auth endpoints.

Registration and sign-in are the only unauthenticated write paths in the app, which
makes them the ones worth protecting: an open /auth/register lets a script create
accounts without limit, and each account can then spend the deployment's LLM budget.

Turnstile is used rather than reCAPTCHA because it is free at any volume, needs no
Google account, and in the common case shows the visitor nothing at all -- it scores
the browser silently and only challenges when something looks automated.

WHAT THIS DOES NOT DO
---------------------
This stops scripted signups. It does not stop a volumetric DDoS: by the time a
request reaches this function it has already consumed a connection and a worker.
Absorbing floods is the job of a network edge in front of the app -- see
SECURITY_SETUP.md. Treating a captcha as DDoS protection is the standard mistake,
so it is written down here rather than left implied.
"""
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

# Cloudflare's documented always-passes secret. Used when TURNSTILE_SECRET_KEY is
# unset so local development and the test suite are not blocked by a captcha.
TESTING_SECRET = "1x0000000000000000000000000000000AA"

TIMEOUT_SECONDS = 6.0


def site_key() -> str:
    """Public key for the widget. Empty means the captcha is switched off."""
    return (os.environ.get("TURNSTILE_SITE_KEY") or "").strip()


def _secret() -> str:
    return (os.environ.get("TURNSTILE_SECRET_KEY") or "").strip()


def enabled() -> bool:
    """True only when BOTH keys are configured.

    Half-configured is treated as off on purpose. A deployment with a secret but no
    site key would reject every real user, since the frontend would have no widget
    to produce a token with -- failing closed there locks out the humans and stops
    none of the bots.
    """
    return bool(site_key() and _secret())


async def verify(token: Optional[str], remote_ip: Optional[str] = None) -> tuple[bool, str]:
    """Check a Turnstile token. Returns (ok, reason).

    Fails CLOSED on a bad or missing token, and OPEN on a network failure reaching
    Cloudflare. That asymmetry is deliberate: refusing a forged token is the whole
    point, but if Cloudflare is unreachable the choice is between letting some bots
    through and locking every real user out of sign-in until it recovers. The
    outage is logged at warning level so it is visible rather than silent.
    """
    if not enabled():
        return True, "captcha_disabled"

    token = (token or "").strip()
    if not token:
        return False, "Complete the human verification check and try again."

    data = {"secret": _secret(), "response": token}
    if remote_ip:
        data["remoteip"] = remote_ip

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            r = await client.post(VERIFY_URL, data=data)
            r.raise_for_status()
            payload = r.json()
    except Exception as exc:
        logger.warning("turnstile.unreachable error=%s -- allowing request", exc)
        return True, "verification_unavailable"

    if payload.get("success"):
        return True, "ok"

    codes = payload.get("error-codes") or []
    logger.info("turnstile.rejected codes=%s", codes)

    # These two mean the visitor's token went stale (a form left open, a retry), which
    # is a normal human situation and deserves a different message from a forgery.
    if {"timeout-or-duplicate", "invalid-input-response"} & set(codes):
        return False, "Your verification expired. Please tick the box again."
    return False, "Human verification failed. Please try again."
