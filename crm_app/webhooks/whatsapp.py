"""WhatsApp message + group-event ingestion.

Auth: shared bearer secret in `X-Webhook-Secret` (constant-time compared).
Dedupe: by `message_id` if sent, otherwise SHA-256 fingerprint of the payload.
Shop binding: group's shop_url first, then sender_phone -> contacts.phone fallback.
"""
import hashlib
import json
import logging
import os
import secrets as _secrets
from typing import Optional, Union

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import WhatsAppGroup, WhatsAppGroupEvent, WhatsAppMessage
from ..schemas import (
    WhatsAppGroupEventIn,
    WhatsAppGroupEventResult,
    WhatsAppMessageBatch,
    WhatsAppMessageIn,
    WhatsAppMessagesResult,
)
from ..utils import build_phone_to_shop, norm_phone, to_naive_utc

logger = logging.getLogger("crm.webhook.whatsapp")
router = APIRouter(prefix="/webhooks/whatsapp", tags=["webhooks"])

WEBHOOK_SECRET = os.environ.get("WHATSAPP_WEBHOOK_SECRET")


def _verify_secret(header_value: Optional[str]):
    if not WEBHOOK_SECRET:
        raise HTTPException(503, "WHATSAPP_WEBHOOK_SECRET not configured on server")
    if not header_value or not _secrets.compare_digest(header_value, WEBHOOK_SECRET):
        raise HTTPException(401, "invalid webhook secret")


def _derive_message_id(m: WhatsAppMessageIn) -> str:
    """Stable fingerprint when the bridge doesn't supply a message_id.
    Same payload retried -> same hash -> upsert."""
    parts = [
        m.group_id or "",
        m.group_name or "",
        m.sender_phone or "",
        m.timestamp.isoformat() if m.timestamp else "",
        m.message_type or "",
        m.body or "",
        m.media_url or "",
    ]
    h = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]
    return f"derived:{h}"


def _resolve_group(
    db: Session, cache: dict, group_jid: Optional[str], group_name: Optional[str]
) -> Optional[WhatsAppGroup]:
    """Find or create a WhatsAppGroup row.
    Order: JID match -> group_name match (single hit) -> new tracking row."""
    if group_jid:
        key = ("jid", group_jid)
        wag = cache.get(key)
        if wag is None:
            wag = db.query(WhatsAppGroup).filter_by(group_jid=group_jid).first()
            if wag is None and group_name:
                # promote a name-only row to also carry the JID
                wag = (
                    db.query(WhatsAppGroup)
                    .filter_by(group_name=group_name, group_jid=None)
                    .first()
                )
                if wag is not None:
                    wag.group_jid = group_jid
            if wag is None:
                wag = WhatsAppGroup(group_jid=group_jid, group_name=group_name)
                db.add(wag)
                db.flush()
            cache[key] = wag
        if group_name and wag.group_name != group_name:
            wag.group_name = group_name
        return wag

    if group_name:
        key = ("name", group_name)
        wag = cache.get(key)
        if wag is None:
            matches = db.query(WhatsAppGroup).filter_by(group_name=group_name).all()
            if len(matches) == 1:
                wag = matches[0]
            elif len(matches) > 1:
                with_shop = [m for m in matches if m.shop_url]
                if len(with_shop) == 1:
                    wag = with_shop[0]
                else:
                    logger.warning(
                        "wa.messages: ambiguous group_name=%r (%d matches); creating tracking row",
                        group_name, len(matches),
                    )
                    wag = WhatsAppGroup(group_name=group_name)
                    db.add(wag); db.flush()
            else:
                wag = WhatsAppGroup(group_name=group_name)
                db.add(wag); db.flush()
            cache[key] = wag
        return wag

    return None


@router.post(
    "/messages",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=WhatsAppMessagesResult,
)
def receive_messages(
    payload: Union[WhatsAppMessageBatch, WhatsAppMessageIn],
    x_webhook_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    _verify_secret(x_webhook_secret)

    items = payload.messages if isinstance(payload, WhatsAppMessageBatch) else [payload]
    phone_to_shop = build_phone_to_shop(db)
    group_cache: dict = {}

    inserted = updated = 0
    failed: list = []
    accepted_ids: list = []

    for m in items:
        mid = m.message_id or _derive_message_id(m)
        try:
            with db.begin_nested():
                wag = _resolve_group(db, group_cache, m.group_id, m.group_name)
                ts = to_naive_utc(m.timestamp)
                if wag and ts and (wag.last_activity_at is None or ts > wag.last_activity_at):
                    wag.last_activity_at = ts

                shop_url = wag.shop_url if wag else None
                if shop_url is None and m.sender_phone:
                    shop_url = phone_to_shop.get(norm_phone(m.sender_phone))

                row = db.get(WhatsAppMessage, mid)
                if row is None:
                    row = WhatsAppMessage(message_id=mid)
                    db.add(row)
                    is_new = True
                else:
                    is_new = False

                row.group_id = m.group_id
                row.group_name = m.group_name
                row.sender_phone = m.sender_phone
                row.sender_name = m.sender_name
                row.timestamp = ts
                row.body = m.body
                row.is_from_me = m.is_from_me
                row.message_type = m.message_type
                row.reply_to_message_id = m.reply_to_message_id
                row.media_url = m.media_url
                row.media_mime_type = m.media_mime_type
                row.media_caption = m.media_caption
                row.is_edited = m.is_edited
                row.is_deleted = m.is_deleted
                row.raw = json.dumps(m.raw, ensure_ascii=False) if m.raw else None
                row.shop_url = shop_url

            if is_new:
                inserted += 1
            else:
                updated += 1
            accepted_ids.append(mid)
        except Exception as e:
            logger.exception("wa.messages: failed message_id=%s", mid)
            failed.append({"message_id": mid, "error": f"{type(e).__name__}: {e}"})

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.exception("wa.messages: outer commit failed")
        raise HTTPException(500, f"commit failed: {type(e).__name__}: {e}")

    logger.info(
        "wa.messages received=%d inserted=%d updated=%d failed=%d",
        len(items), inserted, updated, len(failed),
    )
    return WhatsAppMessagesResult(
        received=len(items),
        inserted=inserted,
        updated=updated,
        failed=failed,
        accepted_ids=accepted_ids,
    )


@router.post(
    "/groups",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=WhatsAppGroupEventResult,
)
def receive_group_event(
    payload: WhatsAppGroupEventIn,
    x_webhook_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    _verify_secret(x_webhook_secret)

    changed_at = to_naive_utc(payload.changed_at)
    ev = WhatsAppGroupEvent(
        event_type=payload.event_type,
        group_id=payload.group_id,
        group_name=payload.group_name,
        members=json.dumps([m.model_dump() for m in payload.members], ensure_ascii=False),
        changed_at=changed_at,
        raw=json.dumps(payload.raw, ensure_ascii=False) if payload.raw else None,
    )
    db.add(ev)

    applied = False
    if payload.event_type in ("group_created", "group_renamed"):
        wag = db.query(WhatsAppGroup).filter_by(group_jid=payload.group_id).first()
        if wag is None:
            wag = WhatsAppGroup(group_jid=payload.group_id, group_name=payload.group_name)
            db.add(wag)
        elif payload.group_name:
            wag.group_name = payload.group_name
        applied = True
    # members_added/removed: log only; membership table not modeled yet

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.exception("wa.groups: commit failed")
        raise HTTPException(500, f"commit failed: {type(e).__name__}: {e}")

    logger.info(
        "wa.groups event=%s group=%s members=%d applied=%s",
        payload.event_type, payload.group_id, len(payload.members), applied,
    )
    return WhatsAppGroupEventResult(event_id=ev.id, group_jid=payload.group_id, applied=applied)
