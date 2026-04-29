"""Native Periskope webhook ingestion.

Path B from the Periskope integration discussion: Periskope POSTs its
raw payloads here directly. We verify their HMAC signature, transform
each `message.created` event into a `whatsapp_raw_messages` row, and
opportunistically learn group names from `chat.created` events so
subsequent messages get a populated `group_name`.

Why this lives alongside `/webhooks/whatsapp/messages` (intern path)
rather than replacing it: the intern's bridge predates the Periskope
decision. The two paths write to the same table and use the same
inline resolver, so downstream consumers (admin tooling, frontend)
can't tell which path produced a given row.

Auth: HMAC-SHA256 of the raw request body, hex-compared (no prefix)
against `x-periskope-signature` header. Secret in `PERISKOPE_SIGNING_SECRET`.

Events handled:
  • message.created            → persisted as a raw message row
  • message.updated            → existing row's body + is_edited updated
                                 (looked up by source_message_id)
  • message.deleted            → existing row's is_deleted set
  • chat.created               → WhatsAppGroup upsert by JID
  • chat.notification.created  → recorded in whatsapp_group_events for
                                 audit; member changes are NOT yet
                                 reflected in contacts (TODO)
  • everything else            → 200 OK, ignored. Periskope's webhook
                                 console doesn't let us scope events
                                 perfectly; returning 200 keeps the
                                 retry queue clean.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..db import get_db, insert_on_conflict_do_nothing
from ..models import WhatsAppGroup, WhatsAppGroupEvent, WhatsAppRawMessage
from ..resolver import resolve_whatsapp_message
from ..time_utils import utcnow_naive
from ..utils import to_naive_utc

logger = logging.getLogger("crm.webhook.periskope")
router = APIRouter(prefix="/webhooks/periskope", tags=["webhooks"])


def _signing_secret() -> Optional[str]:
    return os.environ.get("PERISKOPE_SIGNING_SECRET")


def _verify_signature(body: bytes, header_value: Optional[str]):
    secret = _signing_secret()
    if not secret:
        # Distinct from 401 — we want noisy errors when ops forgets to
        # set the env var, not silent acceptance.
        raise HTTPException(503, "PERISKOPE_SIGNING_SECRET not configured on server")
    if not header_value:
        raise HTTPException(401, "missing x-periskope-signature header")
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, header_value.strip()):
        raise HTTPException(401, "invalid periskope signature")


# ── JID parsing ────────────────────────────────────────────────────────────
# WhatsApp identifiers come in two flavors over Periskope:
#   <digits>@c.us        — individual contact (a real phone)
#   <digits>@g.us        — group chat (NOT a phone, just a group id)
# Strip the suffix to recover the underlying digits.
_JID_RE = re.compile(r"^([\d]+)@(c|g|s|broadcast)\.([a-z]+)$")


def _digits_from_jid(jid: Optional[str]) -> Optional[str]:
    """Pull the digit part out of a WA JID. Returns None if input is
    blank or doesn't match the JID shape (e.g. when Periskope sends an
    already-normalized phone). Keeps non-JID strings untouched so we
    still get something usable downstream."""
    if not jid:
        return None
    m = _JID_RE.match(jid.strip())
    if m:
        return m.group(1)
    # Already a bare phone string — let the existing norm_phone handle it.
    return jid


def _is_group_jid(jid: Optional[str]) -> bool:
    return bool(jid) and "@g.us" in jid


# ── Periskope-ish timestamp parsing ────────────────────────────────────────
# Their docs example: "2024-05-13 11:19:34+00". Python 3.11+ accepts the
# space separator, but `+00` (no minutes) tripped older parsers. Accept a
# few variants defensively.
def _parse_periskope_ts(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    s = s.strip()
    # Normalize "+00" -> "+0000", "+05:30" stays as-is, "Z" -> "+0000"
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    if re.search(r"[+-]\d{2}$", s):  # e.g. "+00" or "-05"
        s = s + "00"
    if re.search(r"[+-]\d{4}$", s):  # e.g. "+0000"
        s = s[:-5] + s[-5:-2] + ":" + s[-2:]  # convert to "+00:00"
    # Python's fromisoformat now handles space separator (3.11+)
    try:
        return to_naive_utc(datetime.fromisoformat(s))
    except (ValueError, TypeError):
        return None


# ── chat_id → group_name cache (via WhatsAppGroup) ─────────────────────────
def _learn_group(db: Session, chat_id: Optional[str], chat_name: Optional[str]):
    """Upsert a WhatsAppGroup row keyed by group_jid=chat_id. We use the
    existing `whatsapp_groups` table (which already has a `group_jid`
    column) so the rest of the system can keep using `group_name` for
    lookups."""
    if not chat_id:
        return
    wag = db.query(WhatsAppGroup).filter_by(group_jid=chat_id).first()
    if wag is None:
        wag = WhatsAppGroup(group_jid=chat_id, group_name=chat_name)
        db.add(wag)
    elif chat_name and wag.group_name != chat_name:
        wag.group_name = chat_name


def _group_name_for(db: Session, chat_id: Optional[str]) -> Optional[str]:
    if not chat_id:
        return None
    wag = db.query(WhatsAppGroup).filter_by(group_jid=chat_id).first()
    if wag and wag.group_name:
        return wag.group_name
    # Fallback: use the JID itself so we never lose the message. The
    # group_name will be filled in when the matching chat.created
    # arrives (or via /chat.custom-properties.updated etc.).
    return chat_id


# ── message_type collapse ──────────────────────────────────────────────────
# Periskope: chat | image | video | audio | document | ptt
# Ours:      text | document
# `chat` is plain text. Everything else is a media-bearing message —
# group as `document` so the existing schema validation passes.
def _collapse_type(periskope_type: Optional[str]) -> str:
    if (periskope_type or "").lower() == "chat":
        return "text"
    return "document"


# ── handlers ───────────────────────────────────────────────────────────────
def _handle_chat_created(data: dict, db: Session) -> dict:
    chat_id = data.get("chat_id") or data.get("id")
    chat_name = data.get("chat_name") or data.get("name")
    _learn_group(db, chat_id, chat_name)
    return {"event": "chat.created", "chat_id": chat_id, "learned_name": bool(chat_name)}


def _handle_message_created(data: dict, db: Session) -> dict:
    """Insert a single message row + run inline resolution. Idempotent
    via the natural-key UNIQUE constraint on
    (group_name, sender_phone, timestamp, body)."""
    chat_id = data.get("chat_id")
    sender_phone_raw = data.get("sender_phone") or data.get("from")
    sender_phone_digits = _digits_from_jid(sender_phone_raw)

    # Group name resolution: if it's a group chat, look up the name we
    # learned from chat.created. If it's a 1:1 chat, the chat_id IS the
    # contact's JID — fall back to the digit form so dedupe still works.
    if _is_group_jid(chat_id):
        group_name = _group_name_for(db, chat_id) or chat_id
    else:
        # Direct chat: use the contact JID as the synthetic group_name
        # so the natural-key dedupe still functions.
        group_name = chat_id or "(unknown chat)"

    ts = _parse_periskope_ts(data.get("timestamp"))
    body = data.get("body") or ""
    is_from_me = bool(data.get("from_me"))
    msg_type = _collapse_type(data.get("message_type"))

    media = data.get("media") if isinstance(data.get("media"), dict) else None
    media_url = media.get("path") if media else None

    if not (sender_phone_digits and ts and group_name):
        # Skip — couldn't extract the minimum required fields. We still
        # 200 to avoid retry storms; the row is logged for later debug.
        logger.warning(
            "periskope.message.created: skipped — missing required fields. "
            "chat_id=%s sender_phone=%s timestamp=%s",
            chat_id, sender_phone_raw, data.get("timestamp"),
        )
        return {"event": "message.created", "skipped": True, "reason": "missing fields"}

    row = {
        "group_name": group_name,
        "sender_phone": sender_phone_digits,
        "sender_name": None,  # Periskope's message payload doesn't include
                              # contact_name. The Chat object has it, but
                              # we'd need a per-message lookup. Phase-2 work.
        "timestamp": ts,
        "body": body,
        "is_from_me": is_from_me,
        "message_type": msg_type,
        "media_url": media_url,
        "source_message_id": data.get("message_id"),  # Periskope's stable id;
                                                      # required for update/delete lookups.
        "received_at": utcnow_naive(),
        "resolution_status": "pending",
    }

    stmt = insert_on_conflict_do_nothing(
        WhatsAppRawMessage, [row],
        ["group_name", "sender_phone", "timestamp", "body"],
        returning=WhatsAppRawMessage.id,
    )
    result = db.execute(stmt)
    inserted_ids = [r[0] for r in result.fetchall()]

    resolved = False
    if inserted_ids:
        # Inline resolution — same path the intern endpoint uses.
        new_row = db.get(WhatsAppRawMessage, inserted_ids[0])
        shop_url, method = resolve_whatsapp_message(
            db,
            sender_phone=new_row.sender_phone,
            group_name=new_row.group_name,
            evidence_table="whatsapp_raw_messages",
            evidence_id=str(new_row.id),
        )
        new_row.processed_at = utcnow_naive()
        if shop_url and shop_url != "conflict":
            new_row.resolved_shop_url = shop_url
            new_row.resolution_status = "resolved"
            new_row.resolution_method = method
            resolved = True
        elif shop_url == "conflict":
            new_row.resolution_status = "conflict"
            new_row.resolution_method = method
        else:
            new_row.resolution_method = method

    return {
        "event": "message.created",
        "inserted": len(inserted_ids),
        "duplicate": 1 if not inserted_ids else 0,
        "resolved": int(resolved),
    }


def _handle_message_updated(data: dict, db: Session) -> dict:
    """Periskope fires this when the user edits a WA message. We update
    the body in-place + flag it as edited so the dashboard can render
    a discreet "(edited)" marker. If we never saw the original
    message.created (we missed an event), log a warning and skip —
    don't synthesize a row from update-only data because we'd be
    guessing at fields like timestamp."""
    msg_id = data.get("message_id")
    new_body = data.get("body")
    if not msg_id:
        logger.warning("periskope.message.updated: missing message_id in payload")
        return {"event": "message.updated", "skipped": True, "reason": "missing message_id"}

    row = (
        db.query(WhatsAppRawMessage)
        .filter_by(source_message_id=msg_id)
        .first()
    )
    if row is None:
        logger.warning("periskope.message.updated: no row for source_message_id=%s "
                       "(probably missed message.created)", msg_id)
        return {"event": "message.updated", "skipped": True, "reason": "row not found"}

    if new_body is not None:
        row.body = new_body
    row.is_edited = True
    row.edited_at = utcnow_naive()
    return {"event": "message.updated", "updated": 1, "row_id": row.id}


def _handle_message_deleted(data: dict, db: Session) -> dict:
    """Soft-delete: keep the row for audit (deleted messages are often
    the most interesting ones in a CS context — what did the client
    say and then retract?), just flag it. The dashboard renders these
    with strikethrough."""
    msg_id = data.get("message_id")
    if not msg_id:
        logger.warning("periskope.message.deleted: missing message_id")
        return {"event": "message.deleted", "skipped": True, "reason": "missing message_id"}

    row = (
        db.query(WhatsAppRawMessage)
        .filter_by(source_message_id=msg_id)
        .first()
    )
    if row is None:
        logger.warning("periskope.message.deleted: no row for source_message_id=%s", msg_id)
        return {"event": "message.deleted", "skipped": True, "reason": "row not found"}

    row.is_deleted = True
    row.deleted_at = utcnow_naive()
    return {"event": "message.deleted", "deleted": 1, "row_id": row.id}


def _handle_chat_notification_created(data: dict, db: Session) -> dict:
    """Group lifecycle events. Verified Periskope payload shape:
        {chat_id, author, type, recipientids[], timestamp, ...}
    where:
      - `type` is the action (e.g. "remove", "add", "rename" …).
      - `author` is the JID of the user who performed it.
      - `recipientids` is the list of JIDs the action targeted.

    We persist the raw payload to `whatsapp_group_events` for audit. The
    `members` column on that table is JSON-encoded; we store recipientids
    there so the existing schema doesn't need changing.

    Add-events SHOULD eventually create candidate contacts under the
    bound merchant (a new person joining a merchant's group is a contact
    we should track). Holding off on that until we see real production
    samples — the `type` enum isn't fully documented."""
    chat_id = data.get("chat_id") or data.get("group_id")
    notif_type = data.get("type") or "unknown"
    author = data.get("author")
    recipientids = data.get("recipientids") or []
    changed_at = _parse_periskope_ts(data.get("timestamp") or data.get("created_at"))

    members_blob = {"author": author, "recipientids": recipientids} if (author or recipientids) else None

    ev = WhatsAppGroupEvent(
        event_type=f"periskope:{notif_type}",
        group_id=chat_id,
        group_name=data.get("chat_name"),
        members=json.dumps(members_blob, ensure_ascii=False) if members_blob else None,
        changed_at=changed_at,
        raw=json.dumps(data, ensure_ascii=False),
    )
    db.add(ev)
    db.flush()
    return {
        "event": "chat.notification.created",
        "recorded": True,
        "event_id": ev.id,
        "type": notif_type,
        "author": author,
        "recipient_count": len(recipientids),
    }


# ── main entrypoint ────────────────────────────────────────────────────────
@router.post("", status_code=status.HTTP_200_OK)
@router.post("/", status_code=status.HTTP_200_OK)
async def receive(
    request: Request,
    x_periskope_signature: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """One URL for all Periskope events — they don't let you split per
    event type. We dispatch internally."""
    body = await request.body()
    _verify_signature(body, x_periskope_signature)

    try:
        payload = json.loads(body or b"{}")
    except json.JSONDecodeError:
        raise HTTPException(400, "invalid JSON body")

    event = (payload.get("event") or "").strip()
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}

    try:
        if event == "message.created":
            result = _handle_message_created(data, db)
        elif event == "message.updated":
            result = _handle_message_updated(data, db)
        elif event == "message.deleted":
            result = _handle_message_deleted(data, db)
        elif event == "chat.created":
            result = _handle_chat_created(data, db)
        elif event == "chat.notification.created":
            result = _handle_chat_notification_created(data, db)
        else:
            # Subscribed by accident, or new event we don't handle yet.
            # 200 + log keeps Periskope's retry queue clean.
            logger.info("periskope: ignoring event=%s", event or "<missing>")
            result = {"event": event or None, "ignored": True}

        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.exception("periskope: handler failed for event=%s", event)
        raise HTTPException(500, f"handler failed: {type(e).__name__}: {e}")

    logger.info("periskope event=%s result=%s", event, result)
    return result
