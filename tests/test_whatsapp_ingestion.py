"""Phase B tests: WhatsApp raw-message webhook contract.

Covers single insert, batch, dedupe, bad secret, oversize batch, bad
schema. Also confirms the inline static-directory resolver lights up
when contacts/groups are seeded.
"""
import pytest


SECRET = "test-secret"
PATH = "/webhooks/whatsapp/messages"


def _msg(ts="2026-04-26T10:00:00Z", body="hello", sender_phone="+919999999999",
         group_name="Acme Test", message_type="text", media_url=None,
         is_from_me=False, sender_name="Aditi"):
    return {
        "group_name": group_name,
        "sender_phone": sender_phone,
        "sender_name": sender_name,
        "timestamp": ts,
        "body": body,
        "is_from_me": is_from_me,
        "message_type": message_type,
        "media_url": media_url,
    }


def _post(client, body, secret=SECRET):
    headers = {"X-Webhook-Secret": secret} if secret else {}
    return client.post(PATH, json=body, headers=headers)


def test_single_insert(tmp_app):
    client = tmp_app["client"]
    r = _post(client, _msg())
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["received"] == 1
    assert j["duplicates"] == 0
    # No contacts seeded → resolution stays pending
    assert j["pending"] == 1
    assert j["resolved"] == 0


def test_batch_insert(tmp_app):
    client = tmp_app["client"]
    body = {"messages": [
        _msg(ts="2026-04-26T10:00:00Z", body="one"),
        _msg(ts="2026-04-26T10:01:00Z", body="two"),
        _msg(ts="2026-04-26T10:02:00Z", body="three"),
    ]}
    r = _post(client, body)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["received"] == 3
    assert j["duplicates"] == 0


def test_dedupe_on_retry(tmp_app):
    """Same natural key (group_name, sender_phone, timestamp, body) →
    second send treated as duplicate, no new row."""
    client = tmp_app["client"]
    msg = _msg(body="dedupe-me")
    r1 = _post(client, msg)
    assert r1.status_code == 200
    assert r1.json()["duplicates"] == 0

    r2 = _post(client, msg)
    assert r2.status_code == 200
    j2 = r2.json()
    assert j2["received"] == 1
    assert j2["duplicates"] == 1


def test_dedupe_works_for_media_only_messages(tmp_app):
    """body=None coerces to empty string server-side so the unique key
    actually catches retries of media-only messages (SQLite's NULL-is-
    distinct rule would otherwise let dupes through)."""
    client = tmp_app["client"]
    msg = _msg(body=None, message_type="document",
               media_url="https://example.com/doc.pdf")
    r1 = _post(client, msg)
    assert r1.status_code == 200
    assert r1.json()["duplicates"] == 0

    r2 = _post(client, msg)
    assert r2.json()["duplicates"] == 1


def test_bad_secret(tmp_app):
    client = tmp_app["client"]
    r = _post(client, _msg(), secret="wrong")
    assert r.status_code == 401
    r2 = _post(client, _msg(), secret=None)
    assert r2.status_code == 401


def test_missing_required_fields_422(tmp_app):
    client = tmp_app["client"]
    # missing sender_phone, timestamp, etc.
    r = _post(client, {"group_name": "x"})
    assert r.status_code == 422


def test_oversize_batch_rejected(tmp_app):
    """Pydantic max_length=500 → 422 at parse time. (The 413 code path
    in the handler is a defence-in-depth fallback.)"""
    client = tmp_app["client"]
    body = {"messages": [
        _msg(ts=f"2026-04-26T10:{i // 60:02d}:{i % 60:02d}Z", body=f"x{i}")
        for i in range(501)
    ]}
    r = _post(client, body)
    assert r.status_code == 422


def test_inline_static_directory_resolution(tmp_app):
    """Seed a contact + shop, then post a message from that phone — the
    handler should mark the row resolved with method=static_directory_phone."""
    client = tmp_app["client"]
    db_module = tmp_app["db_module"]
    from crm_app.models import Contact, Shop, WhatsAppRawMessage

    db = db_module.SessionLocal()
    try:
        db.add(Shop(shop_url="acme.myshopify.com"))
        db.add(Contact(shop_url="acme.myshopify.com", phone="+919999999999"))
        db.commit()
    finally:
        db.close()

    r = _post(client, _msg(sender_phone="+919999999999"))
    assert r.status_code == 200
    j = r.json()
    assert j["resolved"] == 1
    assert j["pending"] == 0

    db = db_module.SessionLocal()
    try:
        row = db.query(WhatsAppRawMessage).first()
        assert row.resolution_status == "resolved"
        assert row.resolved_shop_url == "acme.myshopify.com"
        assert row.resolution_method == "static_directory_phone"
        assert row.processed_at is not None
    finally:
        db.close()


def test_unresolved_stays_pending_not_unresolvable(tmp_app):
    """Spec: failed resolution → 'pending', NOT 'unresolvable'."""
    client = tmp_app["client"]
    db_module = tmp_app["db_module"]
    from crm_app.models import WhatsAppRawMessage

    r = _post(client, _msg())
    assert r.status_code == 200
    db = db_module.SessionLocal()
    try:
        row = db.query(WhatsAppRawMessage).first()
        assert row.resolution_status == "pending"
        assert row.resolution_method == "unresolved"
    finally:
        db.close()
