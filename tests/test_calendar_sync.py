"""Sync-script tests.

We mock `googleapiclient.discovery.build` to return a fake service whose
`.events().list().execute()` returns canned event payloads. Verifies:
  - per-connection failure isolation (a bad token doesn't kill the run)
  - external-attendee → shop_url resolution + add_binding side effect
  - idempotent upsert (re-running yields no extra rows)
  - DWD credentials are built from service-account JSON, not refresh tokens
  - admin enable-dwd flips mode using the SA path
"""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch


def _seed_connection(db_module, **overrides):
    from crm_app.google.crypto import encrypt_token
    from crm_app.models import CalendarConnection
    db = db_module.SessionLocal()
    try:
        conn = CalendarConnection(
            user_email=overrides.get("user_email", "diana@bitespeed.co"),
            auth_mode=overrides.get("auth_mode", "user_oauth"),
            refresh_token_encrypted=encrypt_token("a-refresh-token"),
            access_token="valid-access",
            token_expires_at=datetime.utcnow() + timedelta(hours=1),
            status="active",
        )
        db.add(conn); db.commit()
        return conn.id
    finally:
        db.close()


def _seed_shop_with_email(db_module, shop_url, email):
    """Pre-bind an email→shop edge so the sync script can resolve."""
    from crm_app.identity import add_binding
    from crm_app.models import Shop
    db = db_module.SessionLocal()
    try:
        if not db.get(Shop, shop_url):
            db.add(Shop(shop_url=shop_url))
        add_binding(
            db,
            "email", email,
            "shop_url", shop_url,
            source="static_directory",
            evidence_table="contacts",
            evidence_id="seed",
        )
        db.commit()
    finally:
        db.close()


def _events_list_mock(events):
    """Build a mock service whose .events().list(...).execute() returns
    the supplied events list exactly once, then empty."""
    pages = [{"items": events}, {"items": []}]
    seq = iter(pages)
    fake = MagicMock()
    fake.events.return_value.list.return_value.execute.side_effect = lambda: next(seq)
    return fake


def test_sync_resolves_event_to_shop_and_adds_binding(tmp_app):
    """Calendar event with an external attendee whose email is already
    bound to a shop → event.shop_url set, event.resolution_status='resolved',
    AND a fresh google_calendar binding is added for any other external
    attendees on the call."""
    db_module = tmp_app["db_module"]
    from crm_app.models import Binding, CalendarEvent
    from scripts.sync_google_calendars import sync_one_connection

    # Seed: shop "acme.myshopify.com" and an existing email binding
    _seed_shop_with_email(db_module, "acme.myshopify.com", "ceo@acme.com")
    conn_id = _seed_connection(db_module)

    # The event has TWO external attendees. ceo@acme.com is already
    # bound; finance@acme.com is new — should be auto-bound after resolve.
    event = {
        "id": "google-event-1",
        "summary": "Acme x Bitespeed | Weekly",
        "start": {"dateTime": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()},
        "end":   {"dateTime": (datetime.now(timezone.utc) + timedelta(days=2, minutes=30)).isoformat()},
        "organizer": {"email": "pm@bitespeed.co"},
        "attendees": [
            {"email": "pm@bitespeed.co"},      # internal — skipped
            {"email": "ceo@acme.com"},         # known — resolves the event
            {"email": "finance@acme.com"},     # new — gets bound on success
        ],
        "hangoutLink": "https://meet.google.com/xyz-abcd",
    }

    with patch("scripts.sync_google_calendars.get_calendar_service_for",
               return_value=_events_list_mock([event])):
        db = db_module.SessionLocal()
        try:
            from crm_app.models import CalendarConnection
            conn = db.get(CalendarConnection, conn_id)
            stats = sync_one_connection(db, conn, window_days=30)
        finally:
            db.close()

    assert stats["fetched"] == 1
    assert stats["upserted"] == 1
    assert stats["resolved"] == 1
    assert "error" not in stats

    db = db_module.SessionLocal()
    try:
        row = db.query(CalendarEvent).filter_by(google_event_id="google-event-1").one()
        assert row.shop_url == "acme.myshopify.com"
        assert row.resolution_status == "resolved"

        # The new binding was added for finance@acme.com
        from crm_app.identity import resolve_shop_url_for
        assert resolve_shop_url_for(db, "email", "finance@acme.com") == "acme.myshopify.com"
        # It should also be tagged with source='google_calendar'
        cal_bindings = db.query(Binding).filter_by(source="google_calendar").count()
        assert cal_bindings >= 1
    finally:
        db.close()


def test_sync_idempotent_on_rerun(tmp_app):
    """Running the sync twice with the same event yields one row, not two."""
    db_module = tmp_app["db_module"]
    from crm_app.models import CalendarConnection, CalendarEvent
    from scripts.sync_google_calendars import sync_one_connection

    conn_id = _seed_connection(db_module, user_email="ed@bitespeed.co")
    event = {
        "id": "google-event-rerun",
        "summary": "Some recurring sync",
        "start": {"dateTime": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()},
        "end":   {"dateTime": (datetime.now(timezone.utc) + timedelta(days=1, minutes=30)).isoformat()},
        "organizer": {"email": "ed@bitespeed.co"},
        "attendees": [],
    }

    for _ in range(2):
        with patch("crm_app.google.client.build_credentials_for", return_value=MagicMock()), \
             patch("crm_app.google.client.persist_token_refresh"), \
             patch("googleapiclient.discovery.build", return_value=_events_list_mock([event])):
            db = db_module.SessionLocal()
            try:
                conn = db.get(CalendarConnection, conn_id)
                sync_one_connection(db, conn, window_days=30)
            finally:
                db.close()

    db = db_module.SessionLocal()
    try:
        rows = db.query(CalendarEvent).filter_by(google_event_id="google-event-rerun").all()
        assert len(rows) == 1
    finally:
        db.close()


def test_sync_per_connection_failure_isolation(tmp_app):
    """One connection's auth failure marks IT as failing and surfaces
    last_error, but doesn't crash the orchestrator."""
    db_module = tmp_app["db_module"]
    from crm_app.google.client import CalendarAuthError
    from crm_app.models import CalendarConnection
    from scripts.sync_google_calendars import sync_one_connection

    conn_id = _seed_connection(db_module, user_email="frank@bitespeed.co")

    with patch("scripts.sync_google_calendars.get_calendar_service_for",
               side_effect=CalendarAuthError("invalid_grant: refresh revoked")):
        db = db_module.SessionLocal()
        try:
            conn = db.get(CalendarConnection, conn_id)
            stats = sync_one_connection(db, conn, window_days=30)
            db.refresh(conn)
            assert stats["fetched"] == 0
            assert "error" in stats
            assert conn.status == "revoked"  # 'invalid_grant' → revoked
            assert "invalid_grant" in (conn.last_error or "")
        finally:
            db.close()


def test_dwd_credentials_built_from_service_account(tmp_app, monkeypatch):
    """When auth_mode='dwd_impersonation', build_credentials_for must
    use the service account JSON (not the user refresh token)."""
    db_module = tmp_app["db_module"]
    from crm_app.google.client import build_credentials_for
    from crm_app.models import CalendarConnection

    sa_json = json.dumps({
        "type": "service_account", "project_id": "p", "private_key_id": "k",
        "private_key": "-----BEGIN PRIVATE KEY-----\nFAKE\n-----END PRIVATE KEY-----\n",
        "client_email": "sa@p.iam.gserviceaccount.com", "client_id": "123",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    })
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", sa_json)

    conn_id = _seed_connection(db_module, user_email="grace@bitespeed.co",
                               auth_mode="dwd_impersonation")

    fake_creds = MagicMock()
    fake_creds.with_subject.return_value = "subjected-creds"

    with patch(
        "google.oauth2.service_account.Credentials.from_service_account_info",
        return_value=fake_creds,
    ) as from_info:
        db = db_module.SessionLocal()
        try:
            conn = db.get(CalendarConnection, conn_id)
            result = build_credentials_for(conn)
        finally:
            db.close()

    from_info.assert_called_once()
    fake_creds.with_subject.assert_called_once_with("grace@bitespeed.co")
    assert result == "subjected-creds"


def test_admin_enable_dwd_flips_mode(tmp_app):
    """POST /admin/calendar/enable-dwd flips connections to DWD mode."""
    client = tmp_app["client"]
    db_module = tmp_app["db_module"]

    _seed_connection(db_module, user_email="hank@bitespeed.co")

    # Without service account → 503
    r = client.post("/api/admin/calendar/enable-dwd",
                    json={"user_emails": "all"},
                    headers={"X-Admin-Secret": "admin-test-secret"})
    assert r.status_code == 503

    # With service account configured → flips
    import os
    os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"] = "{\"type\":\"service_account\"}"
    try:
        r = client.post("/api/admin/calendar/enable-dwd",
                        json={"user_emails": ["hank@bitespeed.co"]},
                        headers={"X-Admin-Secret": "admin-test-secret"})
        assert r.status_code == 200, r.text
        assert r.json()["flipped"] == 1

        from crm_app.models import CalendarConnection
        db = db_module.SessionLocal()
        try:
            conn = db.query(CalendarConnection).filter_by(user_email="hank@bitespeed.co").one()
            assert conn.auth_mode == "dwd_impersonation"
        finally:
            db.close()
    finally:
        os.environ.pop("GOOGLE_SERVICE_ACCOUNT_JSON", None)


def test_admin_enable_dwd_rejects_bad_payload(tmp_app):
    client = tmp_app["client"]
    import os
    os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"] = "{\"type\":\"service_account\"}"
    try:
        r = client.post("/api/admin/calendar/enable-dwd",
                        json={"user_emails": 42},
                        headers={"X-Admin-Secret": "admin-test-secret"})
        assert r.status_code == 400
    finally:
        os.environ.pop("GOOGLE_SERVICE_ACCOUNT_JSON", None)
