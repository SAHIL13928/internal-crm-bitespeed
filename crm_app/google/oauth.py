"""Per-user Google OAuth flow.

Endpoints (FastAPI router mounted at root):
  GET  /auth/google/connect        — start the OAuth dance, 302 to Google
  GET  /auth/google/callback       — receive the code, exchange for tokens,
                                     persist a CalendarConnection row
  GET  /auth/google/connections    — list active/failing/revoked connections
  POST /auth/google/disconnect     — revoke + mark a connection as revoked

Anti-CSRF: the OAuth `state` parameter is signed by Starlette's
SessionMiddleware (itsdangerous under the hood). The /callback route
verifies state matches the session before accepting the code.

Scope: `https://www.googleapis.com/auth/calendar.readonly`. We
explicitly add `access_type=offline` + `prompt=consent` so Google
returns a refresh token even after re-consents.
"""
import logging
import os
import secrets as _secrets
import urllib.parse
from typing import Optional

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import CalendarConnection
from ..time_utils import utcnow_naive
from .client import CALENDAR_SCOPE
from .crypto import encrypt_token

logger = logging.getLogger("crm.google.oauth")
router = APIRouter(prefix="/auth/google", tags=["google-oauth"])

# Authlib registry — lazily configured at first use so tests can swap env
# vars before the registration runs.
_oauth_registry: Optional[OAuth] = None


def _registry() -> OAuth:
    global _oauth_registry
    if _oauth_registry is not None:
        return _oauth_registry

    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise HTTPException(
            503,
            "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET not configured on server",
        )

    o = OAuth()
    o.register(
        name="google",
        client_id=client_id,
        client_secret=client_secret,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={
            "scope": f"openid email {CALENDAR_SCOPE}",
            # access_type=offline — request a refresh token
            # prompt=consent — re-issue refresh token even on re-auth
            "access_type": "offline",
            "prompt": "consent",
        },
    )
    _oauth_registry = o
    return o


def _redirect_uri() -> str:
    uri = os.environ.get("GOOGLE_REDIRECT_URI")
    if not uri:
        raise HTTPException(503, "GOOGLE_REDIRECT_URI not configured")
    return uri


# ── endpoints ───────────────────────────────────────────────────────────
@router.get("/connect")
async def connect(request: Request):
    """302-redirect to Google's consent screen. The state cookie is
    signed by SessionMiddleware so /callback can verify CSRF."""
    google = _registry().create_client("google")
    return await google.authorize_redirect(request, _redirect_uri())


@router.get("/callback")
async def callback(request: Request, db: Session = Depends(get_db)):
    """Token exchange. Persists / upserts a CalendarConnection row
    keyed by user_email."""
    google = _registry().create_client("google")
    try:
        token = await google.authorize_access_token(request)
    except OAuthError as e:
        # User declined or some upstream failure. Don't leak details.
        logger.warning("google oauth failed: %s", e)
        raise HTTPException(400, f"oauth failed: {e.error}")

    # Pull email out of the ID token's `userinfo` claim. authlib parses
    # this for us when `openid` was in scope.
    userinfo = token.get("userinfo") or {}
    user_email = userinfo.get("email")
    if not user_email:
        raise HTTPException(400, "google did not return an email claim")

    refresh_token = token.get("refresh_token")
    access_token = token.get("access_token")
    expires_at = None
    if "expires_at" in token:
        from datetime import datetime
        expires_at = datetime.utcfromtimestamp(token["expires_at"])

    if not refresh_token:
        # Google omits refresh_token on subsequent consents unless
        # prompt=consent. We pass that, so this is unusual.
        raise HTTPException(
            400,
            "Google did not return a refresh token. Try disconnecting first, "
            "then reconnect — Google only returns refresh tokens on a fresh consent.",
        )

    conn = db.query(CalendarConnection).filter_by(user_email=user_email).one_or_none()
    if conn is None:
        conn = CalendarConnection(user_email=user_email)
        db.add(conn)
    conn.auth_mode = "user_oauth"
    conn.refresh_token_encrypted = encrypt_token(refresh_token)
    conn.access_token = access_token
    conn.token_expires_at = expires_at
    conn.status = "active"
    conn.last_error = None
    db.commit()

    logger.info("google connected for %s", user_email)
    # After connecting, send the user back to the dashboard.
    return RedirectResponse(url="/app/?google_connected=1", status_code=302)


@router.get("/connections")
def list_connections(db: Session = Depends(get_db)):
    """List all connected accounts. Read-only — fine to expose to the
    dashboard alongside basic-auth."""
    rows = db.query(CalendarConnection).order_by(CalendarConnection.user_email).all()
    return [
        {
            "id": r.id,
            "user_email": r.user_email,
            "auth_mode": r.auth_mode,
            "status": r.status,
            "last_synced_at": r.last_synced_at.isoformat() if r.last_synced_at else None,
            "last_error": r.last_error,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.post("/disconnect")
def disconnect(payload: dict, db: Session = Depends(get_db)):
    """Mark a connection as revoked. We keep the row (and historical
    events) for audit; just stop syncing it. Body: {"user_email": "..."}."""
    user_email = (payload or {}).get("user_email")
    if not user_email:
        raise HTTPException(400, "user_email required")
    conn = db.query(CalendarConnection).filter_by(user_email=user_email).one_or_none()
    if conn is None:
        raise HTTPException(404, f"no connection for {user_email}")
    conn.status = "revoked"
    conn.refresh_token_encrypted = None  # token is now useless to us
    conn.access_token = None
    conn.token_expires_at = None
    db.commit()
    return {"user_email": user_email, "status": "revoked"}
