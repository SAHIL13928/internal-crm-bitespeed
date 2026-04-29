"""Tests for the native Periskope webhook endpoint.

Payloads modeled on the verbatim examples from
https://docs.periskope.app/api-reference/webhooks/message.created.md
and https://docs.periskope.app/api-reference/objects/the-chat-object
"""
import hashlib
import hmac
import json


SECRET = "test-periskope-secret"  # set in conftest.py


def _sign(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _post(client, payload: dict, secret: str = SECRET, signature: str = None):
    body = json.dumps(payload).encode("utf-8")
    sig = signature if signature is not None else _sign(body, secret)
    return client.post(
        "/webhooks/periskope",
        content=body,
        headers={
            "Content-Type": "application/json",
            "x-periskope-signature": sig,
        },
    )


# ── auth ──────────────────────────────────────────────────────────────────
def test_missing_signature_401(tmp_app):
    client = tmp_app["client"]
    body = json.dumps({"event": "message.created", "data": {}}).encode()
    r = client.post("/webhooks/periskope", content=body,
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 401


def test_bad_signature_401(tmp_app):
    client = tmp_app["client"]
    payload = {"event": "message.created", "data": {}}
    r = _post(client, payload, signature="not-a-real-hmac")
    assert r.status_code == 401


# ── message.created (basic happy path) ────────────────────────────────────
def test_message_created_individual_chat(tmp_app):
    """1:1 chat — chat_id is the contact JID. We synthesize group_name
    from chat_id so dedupe still works."""
    client = tmp_app["client"]
    db_module = tmp_app["db_module"]
    from crm_app.models import WhatsAppRawMessage

    payload = {
        "event": "message.created",
        "data": {
            "message_id": "true_9190043@c.us_3EBABC_9190044@c.us",
            "org_id": "org-1",
            "sender_phone": "9190044@c.us",
            "from": "9190044@c.us",
            "chat_id": "9190043@c.us",  # individual chat
            "body": "Hello there",
            "message_type": "chat",
            "from_me": False,
            "timestamp": "2026-04-26 11:19:34+00",
        },
        "org_id": "org-1",
        "timestamp": "2026-04-26 11:19:34+00",
    }
    r = _post(client, payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["event"] == "message.created"
    assert body["inserted"] == 1

    db = db_module.SessionLocal()
    try:
        rows = db.query(WhatsAppRawMessage).all()
        assert len(rows) == 1
        row = rows[0]
        # JID stripped to digits, then norm_phone collapsed (10 digits)
        assert row.sender_phone == "9190044"
        assert row.body == "Hello there"
        assert row.is_from_me is False
        assert row.message_type == "text"  # "chat" → "text"
        assert row.timestamp.year == 2026 and row.timestamp.month == 4
    finally:
        db.close()


# ── chat.created teaches us a group name ──────────────────────────────────
def test_chat_created_then_message_uses_learned_name(tmp_app):
    """1. chat.created arrives with chat_name='Acme <> Bitespeed'.
    2. message.created arrives with the same chat_id but no name.
    Result: stored row has group_name='Acme <> Bitespeed'."""
    client = tmp_app["client"]
    db_module = tmp_app["db_module"]
    from crm_app.models import WhatsAppGroup, WhatsAppRawMessage

    chat_id = "120363972696712345@g.us"

    chat_event = {
        "event": "chat.created",
        "data": {
            "chat_id": chat_id,
            "chat_name": "Acme <> Bitespeed",
            "chat_type": "group",
            "members": {},
        },
    }
    r = _post(client, chat_event)
    assert r.status_code == 200, r.text

    # WhatsAppGroup row was upserted by JID
    db = db_module.SessionLocal()
    try:
        wag = db.query(WhatsAppGroup).filter_by(group_jid=chat_id).first()
        assert wag is not None
        assert wag.group_name == "Acme <> Bitespeed"
    finally:
        db.close()

    msg_event = {
        "event": "message.created",
        "data": {
            "sender_phone": "919001234567@c.us",
            "from": "919001234567@c.us",
            "chat_id": chat_id,
            "body": "deploy update",
            "message_type": "chat",
            "from_me": False,
            "timestamp": "2026-04-26 12:00:00+00",
        },
    }
    r = _post(client, msg_event)
    assert r.status_code == 200, r.text

    db = db_module.SessionLocal()
    try:
        row = db.query(WhatsAppRawMessage).filter_by(body="deploy update").first()
        assert row is not None
        assert row.group_name == "Acme <> Bitespeed"
        # We store the raw digits stripped from the JID (matches the
        # intern path's "store raw, normalize at lookup" convention).
        assert row.sender_phone == "919001234567"
    finally:
        db.close()


# ── media message ─────────────────────────────────────────────────────────
def test_image_message_collapsed_to_document_type(tmp_app):
    """Periskope's "image" / "video" / "audio" / "ptt" all collapse to
    our "document" enum. media.path becomes media_url."""
    client = tmp_app["client"]
    db_module = tmp_app["db_module"]
    from crm_app.models import WhatsAppRawMessage

    payload = {
        "event": "message.created",
        "data": {
            "sender_phone": "919001234567@c.us",
            "from": "919001234567@c.us",
            "chat_id": "119001234567@c.us",
            "body": "",
            "message_type": "image",
            "from_me": False,
            "timestamp": "2026-04-26 13:00:00+00",
            "media": {
                "path": "/storage/v1/object/public/message-media/foo.jpg",
                "mimetype": "image/jpeg",
            },
            "has_media": True,
        },
    }
    r = _post(client, payload)
    assert r.status_code == 200, r.text

    db = db_module.SessionLocal()
    try:
        row = db.query(WhatsAppRawMessage).first()
        assert row.message_type == "document"
        assert row.media_url == "/storage/v1/object/public/message-media/foo.jpg"
    finally:
        db.close()


# ── idempotency ───────────────────────────────────────────────────────────
def test_replay_same_message_dedupes(tmp_app):
    client = tmp_app["client"]
    db_module = tmp_app["db_module"]
    from crm_app.models import WhatsAppRawMessage

    payload = {
        "event": "message.created",
        "data": {
            "sender_phone": "919001234567@c.us",
            "chat_id": "119001234567@c.us",
            "body": "twice",
            "message_type": "chat",
            "from_me": False,
            "timestamp": "2026-04-26 13:30:00+00",
        },
    }
    r1 = _post(client, payload)
    r2 = _post(client, payload)
    assert r1.json()["inserted"] == 1
    assert r2.json()["inserted"] == 0
    assert r2.json()["duplicate"] == 1

    db = db_module.SessionLocal()
    try:
        assert db.query(WhatsAppRawMessage).count() == 1
    finally:
        db.close()


# ── unknown events ────────────────────────────────────────────────────────
def test_unknown_event_returns_200(tmp_app):
    """Periskope's webhook config doesn't let us scope events perfectly.
    Anything we don't handle returns 200 + ignored=True so the retry
    queue stays clean."""
    client = tmp_app["client"]
    payload = {"event": "ticket.created", "data": {"ticket_id": "abc"}}
    r = _post(client, payload)
    assert r.status_code == 200
    assert r.json()["ignored"] is True


# ── inline resolution still works ─────────────────────────────────────────
def test_message_resolves_via_seeded_contact(tmp_app):
    """A pre-seeded contact with this phone should mark the row resolved."""
    client = tmp_app["client"]
    db_module = tmp_app["db_module"]
    from crm_app.models import Contact, Shop, WhatsAppRawMessage

    db = db_module.SessionLocal()
    try:
        db.add(Shop(shop_url="acme.myshopify.com"))
        db.add(Contact(shop_url="acme.myshopify.com", phone="+919001234567"))
        db.commit()
    finally:
        db.close()

    payload = {
        "event": "message.created",
        "data": {
            "sender_phone": "919001234567@c.us",
            "chat_id": "119001234567@c.us",
            "body": "resolve me",
            "message_type": "chat",
            "from_me": False,
            "timestamp": "2026-04-26 14:00:00+00",
        },
    }
    r = _post(client, payload)
    assert r.status_code == 200
    assert r.json()["resolved"] == 1

    db = db_module.SessionLocal()
    try:
        row = db.query(WhatsAppRawMessage).first()
        assert row.resolution_status == "resolved"
        assert row.resolved_shop_url == "acme.myshopify.com"
    finally:
        db.close()


# ── timestamp parsing edge cases ──────────────────────────────────────────
def test_timestamp_with_short_offset(tmp_app):
    """`+00` (no minutes) and `+05:30` should both parse."""
    client = tmp_app["client"]
    db_module = tmp_app["db_module"]
    from crm_app.models import WhatsAppRawMessage

    payload = {
        "event": "message.created",
        "data": {
            "sender_phone": "919001234567@c.us",
            "chat_id": "119001234567@c.us",
            "body": "ist",
            "message_type": "chat",
            "from_me": False,
            "timestamp": "2026-04-26 17:23:13.273371+05:30",
        },
    }
    r = _post(client, payload)
    assert r.status_code == 200, r.text
    db = db_module.SessionLocal()
    try:
        row = db.query(WhatsAppRawMessage).filter_by(body="ist").first()
        assert row is not None
        assert row.timestamp is not None
    finally:
        db.close()


# ── message.updated ────────────────────────────────────────────────────────
def test_message_updated_rewrites_body_and_flags_edited(tmp_app):
    """1. message.created with body='hello'.
    2. message.updated with body='hello world' for the same message_id.
    Result: row's body is updated, is_edited=True, edited_at is set."""
    client = tmp_app["client"]
    db_module = tmp_app["db_module"]
    from crm_app.models import WhatsAppRawMessage

    msg_id = "true_119001234567@c.us_3EBABC_919001234567@c.us"

    create = {
        "event": "message.created",
        "data": {
            "message_id": msg_id,
            "sender_phone": "919001234567@c.us",
            "chat_id": "119001234567@c.us",
            "body": "hello",
            "message_type": "chat",
            "from_me": False,
            "timestamp": "2026-04-26 14:00:00+00",
        },
    }
    r = _post(client, create)
    assert r.status_code == 200

    update = {
        "event": "message.updated",
        "data": {
            "message_id": msg_id,
            "body": "hello world",
            "chat_id": "119001234567@c.us",
        },
    }
    r = _post(client, update)
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == 1

    db = db_module.SessionLocal()
    try:
        row = db.query(WhatsAppRawMessage).filter_by(source_message_id=msg_id).first()
        assert row is not None
        assert row.body == "hello world"
        assert row.is_edited is True
        assert row.edited_at is not None
    finally:
        db.close()


def test_message_updated_for_unknown_id_skips_gracefully(tmp_app):
    """If we missed the original message.created, an update for an
    unknown id should NOT 500 — return 200 + skipped so Periskope's
    retry queue doesn't spin."""
    client = tmp_app["client"]
    update = {
        "event": "message.updated",
        "data": {"message_id": "never-seen-this", "body": "ghost edit"},
    }
    r = _post(client, update)
    assert r.status_code == 200
    assert r.json()["skipped"] is True


# ── message.deleted ────────────────────────────────────────────────────────
def test_message_deleted_soft_deletes_existing_row(tmp_app):
    """Soft-delete: row stays for audit, is_deleted flag set. We
    intentionally keep the body — deleted CS messages are often the
    most interesting ones (what did the client retract?)."""
    client = tmp_app["client"]
    db_module = tmp_app["db_module"]
    from crm_app.models import WhatsAppRawMessage

    msg_id = "true_119001234567@c.us_3EBDEF_919001234567@c.us"

    create = {
        "event": "message.created",
        "data": {
            "message_id": msg_id,
            "sender_phone": "919001234567@c.us",
            "chat_id": "119001234567@c.us",
            "body": "I want to cancel",
            "message_type": "chat",
            "from_me": False,
            "timestamp": "2026-04-26 15:00:00+00",
        },
    }
    _post(client, create)

    delete = {"event": "message.deleted", "data": {"message_id": msg_id}}
    r = _post(client, delete)
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] == 1

    db = db_module.SessionLocal()
    try:
        row = db.query(WhatsAppRawMessage).filter_by(source_message_id=msg_id).first()
        assert row is not None
        # Body preserved for audit, just flagged
        assert row.body == "I want to cancel"
        assert row.is_deleted is True
        assert row.deleted_at is not None
    finally:
        db.close()


# ── chat.notification.created ─────────────────────────────────────────────
def test_chat_notification_recorded(tmp_app):
    """Group lifecycle event lands in whatsapp_group_events for audit.
    Payload shape is verbatim from
    https://docs.periskope.app/api-reference/webhooks/chat.notification.created.md
    """
    client = tmp_app["client"]
    db_module = tmp_app["db_module"]
    from crm_app.models import WhatsAppGroupEvent

    payload = {
        "event": "chat.notification.created",
        "data": {
            "org_id": "2997dd64-89bf-48d3-9a22-b314fca017e5",
            "notification_id": "true_120363371308389685@g.us_fa3c48a78cc64e139e5737f7057235ea_918527184400@c.us_remove",
            "chat_id": "120363371308389685@g.us",
            "author": "918527184400@c.us",
            "type": "remove",
            "timestamp": "2025-01-11T11:52:27.231+00:00",
            "recipientids": ["919537851844@c.us"],
            "org_phone": "918527184400@c.us",
        },
        "org_id": "2997dd64-89bf-48d3-9a22-b314fca017e5",
        "timestamp": "2025-01-11T11:52:27.231+00:00",
    }
    r = _post(client, payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["recorded"] is True
    assert body["type"] == "remove"
    assert body["author"] == "918527184400@c.us"
    assert body["recipient_count"] == 1

    db = db_module.SessionLocal()
    try:
        ev = (
            db.query(WhatsAppGroupEvent)
            .filter_by(group_id="120363371308389685@g.us")
            .order_by(WhatsAppGroupEvent.id.desc())
            .first()
        )
        assert ev is not None
        assert ev.event_type == "periskope:remove"
        # author + recipientids stored together as the members blob
        assert "919537851844" in (ev.members or "")
        assert "918527184400" in (ev.members or "")  # author included
    finally:
        db.close()
