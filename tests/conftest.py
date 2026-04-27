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

    # Wipe any cached crm_app modules so they re-bind to the new DB path.
    for mod in list(sys.modules.keys()):
        if mod == "crm_app" or mod.startswith("crm_app."):
            del sys.modules[mod]

    from fastapi.testclient import TestClient
    from crm_app.main import app
    from crm_app import db as db_mod

    client = TestClient(app)
    yield {"client": client, "db_module": db_mod, "db_path": db_path}

    # Clean up engine connection pool so the temp file can be deleted on Windows.
    db_mod.engine.dispose()
