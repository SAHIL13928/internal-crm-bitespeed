"""HTTP Basic authentication for the read API + dashboard.

Webhooks (`/webhooks/*`) keep their own per-provider secret schemes and
are NOT protected by this. Health (`/api/health`) is intentionally
unprotected so external monitoring can poll it without credentials.

Configuration:
    API_USERNAME, API_PASSWORD env vars

If either is missing, every protected route returns 503 (configuration
missing). This is louder than silent allow-all and forces ops to set
the values before going live.

Usage in route definitions:
    from .auth import require_basic_auth

    @app.get("/api/something", dependencies=[Depends(require_basic_auth)])
    def something(): ...

Or apply globally — see crm_app/main.py.
"""
import hmac
import os
import secrets as _secrets
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

_security = HTTPBasic(auto_error=False)


def require_basic_auth(
    credentials: Optional[HTTPBasicCredentials] = Depends(_security),
):
    """FastAPI dependency. Raises 401 on missing/wrong creds, 503 if
    server is mis-configured."""
    expected_user = os.environ.get("API_USERNAME")
    expected_pass = os.environ.get("API_PASSWORD")
    if not expected_user or not expected_pass:
        raise HTTPException(
            status_code=503,
            detail="API_USERNAME / API_PASSWORD not configured on server",
        )
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="basic auth required",
            headers={"WWW-Authenticate": 'Basic realm="cs-crm"'},
        )
    # Constant-time compare both fields. Pad both sides via secrets.compare_digest
    # to avoid timing leaks even on length differences.
    user_ok = hmac.compare_digest(credentials.username, expected_user)
    pass_ok = hmac.compare_digest(credentials.password, expected_pass)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=401,
            detail="invalid credentials",
            headers={"WWW-Authenticate": 'Basic realm="cs-crm"'},
        )
    return credentials.username
