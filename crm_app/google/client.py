"""Mode-aware Google Calendar service factory.

A `CalendarConnection` row picks one of two auth modes:

  - `user_oauth`         — we hold the user's encrypted refresh token,
                           refresh access tokens on demand
  - `dwd_impersonation`  — we use the org's service account JSON, with
                           `.with_subject(user_email)`, no per-user
                           token needed (super admin pre-authorized
                           the SA in Workspace's domain-wide delegation
                           settings)

The factory `get_calendar_service_for(user_email, db)` returns a
`googleapiclient.discovery.Resource` ready to call `events().list()`
regardless of mode. Callers never have to branch on auth mode.

If the connection's refresh token is invalid or revoked, the helper
flips status='failing' (or 'revoked') with `last_error` and raises
`CalendarAuthError` so the sync loop can keep going on the next user.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from ..models import CalendarConnection
from ..time_utils import utcnow_naive
from .crypto import decrypt_token, encrypt_token

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"


class CalendarAuthError(RuntimeError):
    """Raised when a connection cannot mint credentials. The caller is
    expected to mark the connection as failing/revoked and continue."""


# ── credentials builders ────────────────────────────────────────────────
def _user_oauth_credentials(conn: CalendarConnection):
    """Build google.oauth2.credentials.Credentials from a user_oauth row.
    Refreshes the access token if expired."""
    from google.auth.transport.requests import Request as _GReq
    from google.oauth2.credentials import Credentials

    refresh = decrypt_token(conn.refresh_token_encrypted or "")
    if not refresh:
        raise CalendarAuthError("missing or undecryptable refresh token")

    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise CalendarAuthError("GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET not configured")

    creds = Credentials(
        token=conn.access_token,
        refresh_token=refresh,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=[CALENDAR_SCOPE],
    )

    # Force refresh if we don't have a token or it's about to expire.
    if not creds.token or (conn.token_expires_at and conn.token_expires_at <= utcnow_naive() + timedelta(minutes=2)):
        try:
            creds.refresh(_GReq())
        except Exception as e:  # noqa: BLE001 — refresh-failure is the whole point we trap here
            raise CalendarAuthError(f"refresh failed: {type(e).__name__}: {e}") from e

    return creds


def _dwd_credentials(user_email: str):
    """Build google.oauth2.service_account.Credentials and impersonate
    the given user via Domain-Wide Delegation."""
    from google.oauth2 import service_account

    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not sa_json:
        raise CalendarAuthError("GOOGLE_SERVICE_ACCOUNT_JSON not configured — cannot use DWD")

    try:
        sa_info = json.loads(sa_json)
    except json.JSONDecodeError as e:
        raise CalendarAuthError(f"GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON: {e}") from e

    creds = service_account.Credentials.from_service_account_info(
        sa_info, scopes=[CALENDAR_SCOPE],
    ).with_subject(user_email)
    return creds


# ── public API ──────────────────────────────────────────────────────────
def build_credentials_for(conn: CalendarConnection):
    """Pick the right credentials builder based on `conn.auth_mode`.
    Raises `CalendarAuthError` on unrecoverable problems."""
    if conn.auth_mode == "dwd_impersonation":
        return _dwd_credentials(conn.user_email)
    if conn.auth_mode == "user_oauth":
        return _user_oauth_credentials(conn)
    raise CalendarAuthError(f"unknown auth_mode: {conn.auth_mode!r}")


def persist_token_refresh(db: Session, conn: CalendarConnection, creds) -> None:
    """If `creds` is a user_oauth Credentials object that just refreshed,
    persist the new access token + expiry. No-op for service-account
    creds (those have no refresh token to track)."""
    if conn.auth_mode != "user_oauth":
        return
    token = getattr(creds, "token", None)
    expiry = getattr(creds, "expiry", None)
    if token:
        conn.access_token = token
    if expiry:
        # google's `expiry` is naive UTC already
        conn.token_expires_at = expiry
    # Also re-encrypt the refresh token if google rotated it.
    new_refresh = getattr(creds, "refresh_token", None)
    if new_refresh:
        existing = decrypt_token(conn.refresh_token_encrypted or "")
        if existing != new_refresh:
            conn.refresh_token_encrypted = encrypt_token(new_refresh)
    db.flush()


def get_calendar_service_for(user_email: str, db: Session):
    """Top-level factory used by the sync script. Returns a
    `googleapiclient.discovery.Resource` ready for `.events()`,
    `.calendarList()` etc. Mode is read from the DB; switching modes
    is just a column update — no rebuild."""
    from googleapiclient.discovery import build  # noqa: WPS433

    conn = db.query(CalendarConnection).filter_by(user_email=user_email).one_or_none()
    if conn is None:
        raise CalendarAuthError(f"no calendar_connection row for {user_email}")
    if conn.status == "revoked":
        raise CalendarAuthError(f"connection revoked for {user_email}")

    creds = build_credentials_for(conn)
    persist_token_refresh(db, conn, creds)
    # cache_discovery=False avoids a noisy warning about file caching
    # when running outside the canonical googleapiclient install layout.
    return build("calendar", "v3", credentials=creds, cache_discovery=False)
