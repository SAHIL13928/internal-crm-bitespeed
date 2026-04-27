"""Phase C tests: identity graph operations.

Covers:
  - idempotent add_binding (no dupes by natural key)
  - two-hop resolution
  - conflict detection (>1 shop_url in component)
  - reprocessor revisits previously-pending rows after a new binding
"""
import os
import subprocess
import sys


def test_add_binding_idempotent(tmp_app):
    db_module = tmp_app["db_module"]
    from crm_app.identity import add_binding
    from crm_app.models import Binding

    db = db_module.SessionLocal()
    try:
        b1 = add_binding(
            db, "phone", "+919999999999", "shop_url", "acme.com",
            source="static_directory", confidence=1.0,
            evidence_table="contacts", evidence_id="42",
        )
        b2 = add_binding(
            db, "phone", "+919999999999", "shop_url", "acme.com",
            source="static_directory", confidence=1.0,
            evidence_table="contacts", evidence_id="42",
        )
        assert b1 is not None
        assert b2.id == b1.id  # same row returned
        assert db.query(Binding).count() == 1
    finally:
        db.close()


def test_self_edge_skipped(tmp_app):
    db_module = tmp_app["db_module"]
    from crm_app.identity import add_binding
    from crm_app.models import Binding

    db = db_module.SessionLocal()
    try:
        result = add_binding(
            db, "phone", "+91111", "phone", "+91111",
            source="manual", evidence_id="x",
        )
        assert result is None  # same identity → self edge
        assert db.query(Binding).count() == 0
    finally:
        db.close()


def test_two_hop_resolution(tmp_app):
    """phone A ↔ group_name X ↔ shop_url acme.
    Querying phone A should resolve through 2 hops to acme."""
    db_module = tmp_app["db_module"]
    from crm_app.identity import add_binding, resolve_shop_url_for

    db = db_module.SessionLocal()
    try:
        add_binding(
            db, "phone", "+919999999999", "group_name", "Acme Group",
            source="whatsapp", evidence_table="whatsapp_raw_messages", evidence_id="1",
        )
        add_binding(
            db, "group_name", "Acme Group", "shop_url", "acme.com",
            source="static_directory", evidence_table="whatsapp_groups", evidence_id="g7",
        )
        db.commit()

        result = resolve_shop_url_for(db, "phone", "+919999999999")
        assert result == "acme.com"
    finally:
        db.close()


def test_conflict_when_two_shop_urls_in_component(tmp_app):
    """If a phone is bound to two different shop_urls (real conflict in
    the static directory), resolution returns the CONFLICT sentinel."""
    db_module = tmp_app["db_module"]
    from crm_app.identity import CONFLICT, add_binding, resolve_shop_url_for

    db = db_module.SessionLocal()
    try:
        add_binding(
            db, "phone", "+919999999999", "shop_url", "acme.com",
            source="static_directory", evidence_table="contacts", evidence_id="1",
        )
        add_binding(
            db, "phone", "+919999999999", "shop_url", "beta.com",
            source="static_directory", evidence_table="contacts", evidence_id="2",
        )
        db.commit()
        result = resolve_shop_url_for(db, "phone", "+919999999999")
        assert result == CONFLICT
    finally:
        db.close()


def test_phone_normalization(tmp_app):
    """'+91 999-999-9999' and '+919999999999' are the same identity."""
    db_module = tmp_app["db_module"]
    from crm_app.identity import add_binding, resolve_shop_url_for
    from crm_app.models import Identity

    db = db_module.SessionLocal()
    try:
        add_binding(
            db, "phone", "+91 999-999-9999", "shop_url", "acme.com",
            source="static_directory", evidence_table="contacts", evidence_id="1",
        )
        # Same logical phone, different formatting
        add_binding(
            db, "phone", "+919999999999", "shop_url", "acme.com",
            source="static_directory", evidence_table="contacts", evidence_id="1",
        )
        db.commit()
        # Only one identity row should exist for the phone
        phone_idents = db.query(Identity).filter_by(kind="phone").all()
        assert len(phone_idents) == 1
        assert resolve_shop_url_for(db, "phone", "+91-9999999999") == "acme.com"
    finally:
        db.close()


def test_indian_phone_with_and_without_country_code_match(tmp_app):
    """Regression: '9999999999' (10-digit) and '919999999999' (12-digit
    with country code) must resolve to the same shop. This was broken
    until we canonicalized norm_phone to last-10-digits."""
    db_module = tmp_app["db_module"]
    from crm_app.identity import add_binding, resolve_shop_url_for
    from crm_app.models import Contact, Identity, Shop
    from crm_app.utils import build_phone_to_shop, norm_phone

    # 1. Identity-graph path
    db = db_module.SessionLocal()
    try:
        add_binding(
            db, "phone", "9999999999", "shop_url", "acme.com",
            source="static_directory", evidence_table="contacts", evidence_id="1",
        )
        db.commit()
        # Same logical number, sent in different formats
        assert resolve_shop_url_for(db, "phone", "919999999999") == "acme.com"
        assert resolve_shop_url_for(db, "phone", "+91 99999 99999") == "acme.com"
        # And only one phone identity exists
        assert db.query(Identity).filter_by(kind="phone").count() == 1
    finally:
        db.close()

    # 2. phone_to_shop static-directory path (the FreJun call binder)
    db = db_module.SessionLocal()
    try:
        db.add(Shop(shop_url="beta.com"))
        db.add(Contact(shop_url="beta.com", phone="+91 88888 88888"))
        db.commit()
        p2s = build_phone_to_shop(db)
        # Lookups in either format should both hit beta.com
        assert p2s.get(norm_phone("8888888888")) == "beta.com"
        assert p2s.get(norm_phone("918888888888")) == "beta.com"
        assert p2s.get(norm_phone("+91 88888 88888")) == "beta.com"
    finally:
        db.close()


def test_reprocessor_revisits_pending_after_new_binding(tmp_app):
    """Send an unresolvable WA message (no contact seeded → pending). Then
    add a binding manually and run the reprocess script. The previously-
    pending row should now be resolved."""
    client = tmp_app["client"]
    db_module = tmp_app["db_module"]
    from crm_app.identity import add_binding
    from crm_app.models import WhatsAppRawMessage

    # 1. Send a message — no contact, so it goes pending.
    body = {
        "group_name": "Reprocess Test",
        "sender_phone": "+918888888888",
        "sender_name": "Mystery Caller",
        "timestamp": "2026-04-26T11:00:00Z",
        "body": "wake me up later",
        "is_from_me": False,
        "message_type": "text",
    }
    r = client.post(
        "/webhooks/whatsapp/messages",
        json=body,
        headers={"X-Webhook-Secret": "test-secret"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["pending"] == 1

    # 2. Manually add the binding that would have resolved the message.
    db = db_module.SessionLocal()
    try:
        from crm_app.models import Shop
        db.add(Shop(shop_url="lateshop.com"))
        db.commit()
        add_binding(
            db, "phone", "+918888888888", "shop_url", "lateshop.com",
            source="manual", evidence_table="manual", evidence_id="op-1",
        )
        db.commit()
    finally:
        db.close()

    # 3. Run the reprocess script.
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = os.path.join(repo_root, "scripts", "reprocess_pending.py")
    env = os.environ.copy()
    env["CRM_DB_PATH"] = str(tmp_app["db_path"])
    proc = subprocess.run(
        [sys.executable, script],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr

    # 4. Confirm the row is now resolved.
    db = db_module.SessionLocal()
    try:
        row = (
            db.query(WhatsAppRawMessage)
            .filter_by(group_name="Reprocess Test")
            .first()
        )
        assert row.resolution_status == "resolved"
        assert row.resolved_shop_url == "lateshop.com"
        assert row.resolution_method == "identity_graph"
    finally:
        db.close()


def test_admin_conflicts_endpoint(tmp_app):
    client = tmp_app["client"]
    db_module = tmp_app["db_module"]
    from crm_app.identity import add_binding

    db = db_module.SessionLocal()
    try:
        add_binding(
            db, "phone", "+917777777777", "shop_url", "alpha.com",
            source="static_directory", evidence_table="contacts", evidence_id="1",
        )
        add_binding(
            db, "phone", "+917777777777", "shop_url", "bravo.com",
            source="static_directory", evidence_table="contacts", evidence_id="2",
        )
        db.commit()
    finally:
        db.close()

    # Bad secret → 401
    r = client.get("/admin/conflicts", headers={"X-Admin-Secret": "wrong"})
    assert r.status_code == 401

    # Correct secret → conflict listed
    r = client.get("/admin/conflicts", headers={"X-Admin-Secret": "admin-test-secret"})
    assert r.status_code == 200, r.text
    j = r.json()
    items = j["identity_graph_conflicts"]
    assert any(set(it["shop_urls"]) == {"alpha.com", "bravo.com"} for it in items)
