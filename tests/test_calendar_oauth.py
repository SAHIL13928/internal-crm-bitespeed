"""OAuth callback + token refresh tests.

We don't drive a real Google OAuth round-trip; instead we monkey-patch
authlib's `authorize_access_token` to return a synthetic token, and
verify our /callback route persists the right CalendarConnection row.
For token refresh we mock `Credentials.refresh` and assert we update
the access_token + token_expires_at.
"""
from datetime import datetime, timedelta
from unittest.mock import patch


def test_disconnect_marks_revoked_and_clears_token(tmp_app):
    client = tmp_app["client"]
    db_module = tmp_app["db_module"]
    from crm_app.models import CalendarConnection
    from crm_app.google.crypto import encrypt_token

    db = db_module.SessionLocal()
    try:
        conn = CalendarConnection(
            user_email="alice@bitespeed.co",
            auth_mode="user_oauth",
            refresh_token_encrypted=encrypt_token("super-secret-refresh"),
            access_token="access-A",
            status="active",
        )
        db.add(conn); db.commit()
    finally:
        db.close()

    r = client.post("/auth/google/disconnect", json={"user_email": "alice@bitespeed.co"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "revoked"

    db = db_module.SessionLocal()
    try:
        row = db.query(CalendarConnection).filter_by(user_email="alice@bitespeed.co").one()
        assert row.status == "revoked"
        assert row.refresh_token_encrypted is None
        assert row.access_token is None
    finally:
        db.close()


def test_callback_persists_connection_with_encrypted_refresh_token(tmp_app):
    """Mock authlib's token exchange; verify /callback writes a
    CalendarConnection row, encrypts the refresh token, marks active."""
    client = tmp_app["client"]
    db_module = tmp_app["db_module"]
    from crm_app.models import CalendarConnection
    from crm_app.google.crypto import decrypt_token

    fake_token = {
        "access_token": "ya29.fake_access",
        "refresh_token": "1//fake_refresh_token_value",
        "expires_at": int((datetime.utcnow() + timedelta(hours=1)).timestamp()),
        "userinfo": {"email": "bob@bitespeed.co", "email_verified": True},
    }

    async def fake_authorize_access_token(self, request):
        return fake_token

    with patch(
        "authlib.integrations.starlette_client.StarletteOAuth2App.authorize_access_token",
        new=fake_authorize_access_token,
    ):
        # State and code are real OAuth params; with our mocked token
        # exchange the values don't have to round-trip cryptographically.
        r = client.get("/auth/google/callback?state=anything&code=anything",
                       follow_redirects=False)
    assert r.status_code in (302, 307), r.text

    db = db_module.SessionLocal()
    try:
        row = db.query(CalendarConnection).filter_by(user_email="bob@bitespeed.co").one()
        assert row.status == "active"
        assert row.auth_mode == "user_oauth"
        assert row.refresh_token_encrypted is not None
        assert row.refresh_token_encrypted != "1//fake_refresh_token_value"  # encrypted, not stored plaintext
        # Round-trip: we can decrypt back to the original
        assert decrypt_token(row.refresh_token_encrypted) == "1//fake_refresh_token_value"
        assert row.access_token == "ya29.fake_access"
    finally:
        db.close()


def test_token_refresh_updates_access_token_and_expiry(tmp_app):
    """If the access token is expired, get_calendar_service_for should
    call creds.refresh() and persist the new token + expiry."""
    db_module = tmp_app["db_module"]
    from crm_app.google.client import get_calendar_service_for
    from crm_app.google.crypto import encrypt_token
    from crm_app.models import CalendarConnection

    db = db_module.SessionLocal()
    try:
        conn = CalendarConnection(
            user_email="charlie@bitespeed.co",
            auth_mode="user_oauth",
            refresh_token_encrypted=encrypt_token("refresh-charlie"),
            access_token="expired-access-token",
            token_expires_at=datetime.utcnow() - timedelta(hours=1),
            status="active",
        )
        db.add(conn); db.commit()
    finally:
        db.close()

    new_expiry = datetime.utcnow() + timedelta(hours=1)

    def fake_refresh(self, request):
        self.token = "new-access-token-after-refresh"
        self.expiry = new_expiry

    with patch("google.oauth2.credentials.Credentials.refresh", new=fake_refresh):
        with patch("googleapiclient.discovery.build") as mock_build:
            mock_build.return_value = "mock-calendar-service"
            db = db_module.SessionLocal()
            try:
                service = get_calendar_service_for("charlie@bitespeed.co", db)
                assert service == "mock-calendar-service"
                row = db.query(CalendarConnection).filter_by(user_email="charlie@bitespeed.co").one()
                assert row.access_token == "new-access-token-after-refresh"
                # Allow a 1-second tolerance for any clock drift in the assignment
                assert abs((row.token_expires_at - new_expiry).total_seconds()) < 2
            finally:
                db.close()
