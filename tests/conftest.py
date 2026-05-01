"""Pytest fixtures.

Each test gets a fresh SQLite DB in tmp_path. We achieve this by setting
CRM_DB_PATH BEFORE importing crm_app — the engine reads it at module
load — and then forcibly resetting the engine for every test session.
"""
import importlib
import os
import sys

import pytest


@pytest.fixture(scope="function")
def tmp_app(tmp_path, monkeypatch):
    """Builds a fresh FastAPI app + TestClient pointed at a new SQLite file
    living under tmp_path. Used by ingestion / identity tests."""
    db_path = tmp_path / "crm.db"
    monkeypatch.setenv("CRM_DB_PATH", str(db_path))
    monkeypatch.setenv("WHATSAPP_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("FREJUN_WEBHOOK_SECRET", "frejun-test-secret")
    monkeypatch.setenv("ADMIN_SECRET", "admin-test-secret")
    monkeypatch.setenv("PERISKOPE_SIGNING_SECRET", "test-periskope-secret")
    # API basic auth — tests don't hit protected routes (only webhooks
    # and admin-secret-guarded paths), but set these to silence the
    # 503-when-unconfigured path if a future test does.
    monkeypatch.setenv("API_USERNAME", "test-user")
    monkeypatch.setenv("API_PASSWORD", "test-pass")
    # Google Calendar — fixed test Fernet key so encrypted payloads in
    # tests are deterministic.
    monkeypatch.setenv("CALENDAR_TOKEN_ENCRYPTION_KEY",
                       "test-key-AAAAAAAAAAAAAAAAAAAAAAAAAAAA-AA=")
    # Use a real Fernet key (the one above is invalid format). Generate
    # a deterministic test key via Fernet so encryption/decryption works.
    from cryptography.fernet import Fernet
    monkeypatch.setenv("CALENDAR_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://testserver/auth/google/callback")
    monkeypatch.setenv("GOOGLE_WORKSPACE_DOMAIN", "bitespeed.co")

    # Wipe any cached crm_app and scripts modules so they re-bind to
    # the new DB path AND pick up fresh references to crm_app symbols
    # (otherwise scripts.X.get_thing keeps a stale ref to the previous
    # test's crm_app.thing).
    for mod in list(sys.modules.keys()):
        if mod == "crm_app" or mod.startswith("crm_app.") or mod == "scripts" or mod.startswith("scripts."):
            del sys.modules[mod]

    from fastapi.testclient import TestClient
    from crm_app.main import app
    from crm_app import db as db_mod

    client = TestClient(app)
    yield {"client": client, "db_module": db_mod, "db_path": db_path}

    # Clean up engine connection pool so the temp file can be deleted on Windows.
    db_mod.engine.dispose()
