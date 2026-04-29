"""WhatsApp ingestion endpoints.

`/messages` — receives the WA bridge intern's payload. Lands rows in the
canonical `whatsapp_raw_messages` table (idempotent on intern retries via
the natural-key unique constraint), then inline-resolves each row to a
shop via the static directory + identity graph. Unresolved rows are left
as 'pending' so the graph reprocessor can revisit them once new bindings
appear.

`/groups` — kept for legacy compatibility; the bridge currently does not
feed it but we keep it wired for later membership work.
"""
import hmac
import json
import logging
import os
from datetime import datetime
from typing import List, Optional, Union

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from ..db import get_db, insert_on_conflict_do_nothing
from ..models import WhatsAppGroup, WhatsAppGroupEvent, WhatsAppRawMessage
from ..resolver import resolve_whatsapp_message
from ..schemas import (
    WhatsAppGroupEventIn,
    WhatsAppGroupEventResult,
    WhatsAppRawMessageBatch,
    WhatsAppRawMessageIn,
    WhatsAppRawMessagesResult,
)
from ..time_utils import utcnow_naive
from ..utils import to_naive_utc

logger = logging.getLogger("crm.webhook.whatsapp")
router = APIRouter(prefix="/webhooks/whatsapp", tags=["webhooks"])

# Spec-mandated batch ceiling. Anything larger is rejected with 413 so the
# intern's retry/backoff treats it as a non-retryable fatal condition.
MAX_BATCH_SIZE = 500


def _secret() -> Optional[str]:
    # Read at call time so tests can swap env vars after import.
    return os.environ.get("WHATSAPP_WEBHOOK_SECRET")


def _verify_secret(header_value: Optional[str]):
    secret = _secret()
    if not secret:
        # 503 distinguishes "we forgot to configure" from "you sent the wrong key".
        raise HTTPException(503, "WHATSAPP_WEBHOOK_SECRET not configured on server")
    if not header_value or not hmac.compare_digest(header_value, secret):
        raise HTTPException(401, "invalid webhook secret")


def _coerce_body(b: Optional[str]) -> str:
    """Normalize None → "" so the SQLite UNIQUE(group_name, sender_phone,
    timestamp, body) constraint actually catches retries of media-only
    messages. NULL is treated as distinct in SQLite."""
    return b if b is not None else ""


def _to_row(m: WhatsAppRawMessageIn) -> dict:
    return {
        "group_name": m.group_name,
        "sender_phone": m.sender_phone,
        "sender_name": m.sender_name,
        "timestamp": to_naive_utc(m.timestamp),
        "body": _coerce_body(m.body),
        "is_from_me": m.is_from_me,
        "message_type": m.message_type,
        "media_url": m.media_url,
        "received_at": utcnow_naive(),
        "resolution_status": "pending",
    }


@router.post(
    "/messages",
    status_code=status.HTTP_200_OK,
    response_model=WhatsAppRawMessagesResult,
)
async def receive_messages(
    payload: Union[WhatsAppRawMessageBatch, WhatsAppRawMessageIn],
    x_webhook_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Ingest one message or a batch of up to MAX_BATCH_SIZE. Idempotent.

    Status semantics (matches intern spec):
      - 200  on success (per-row dedupe is normal, not an error)
      - 401  bad / missing secret
      - 422  schema violation (FastAPI handles this before we run)
      - 413  batch larger than MAX_BATCH_SIZE
      - 5xx  real DB / commit error — intern's backoff retries kick in
    """
    _verify_secret(x_webhook_secret)

    items: List[WhatsAppRawMessageIn] = (
        payload.messages if isinstance(payload, WhatsAppRawMessageBatch) else [payload]
    )

    # Pydantic max_length=500 already gives us 422 for oversize batches at
    # parse time. We re-check here to emit 413 (per spec) when callers
    # somehow build the request manually past that boundary.
    if len(items) > MAX_BATCH_SIZE:
        raise HTTPException(413, f"batch too large: {len(items)} > {MAX_BATCH_SIZE}")

    rows = [_to_row(m) for m in items]

    # Bulk INSERT ... ON CONFLICT DO NOTHING. The natural-key constraint
    # (group_name, sender_phone, timestamp, body) makes this idempotent.
    # RETURNING gives us the count of actually-inserted rows so we can
    # report duplicates without re-querying. Dialect-agnostic helper so
    # the same code works on SQLite (local) and Postgres (production).
    try:
        if rows:
            stmt = insert_on_conflict_do_nothing(
                WhatsAppRawMessage, rows,
                ["group_name", "sender_phone", "timestamp", "body"],
                returning=WhatsAppRawMessage.id,
            )
            result = db.execute(stmt)
            inserted_ids = [row[0] for row in result.fetchall()]
        else:
            inserted_ids = []
        db.flush()
    except Exception as e:
        db.rollback()
        logger.exception("wa.messages: bulk insert failed")
        # Real DB error → 5xx so the intern's backoff retries.
        raise HTTPException(500, f"insert failed: {type(e).__name__}: {e}")

    inserted_count = len(inserted_ids)
    duplicates = len(rows) - inserted_count

    # Inline resolution — only for the rows we just inserted. Skipping
    # already-existing rows here is the right call: their resolution_status
    # was handled when they were originally inserted. The reprocess script
    # is the way to revisit them.
    resolved = pending = 0
    if inserted_ids:
        new_rows = (
            db.query(WhatsAppRawMessage)
            .filter(WhatsAppRawMessage.id.in_(inserted_ids))
            .all()
        )
        for r in new_rows:
            shop_url, method = resolve_whatsapp_message(
                db,
                sender_phone=r.sender_phone,
                group_name=r.group_name,
                evidence_table="whatsapp_raw_messages",
                evidence_id=str(r.id),
            )
            r.processed_at = utcnow_naive()
            if shop_url and shop_url != "conflict":
                r.resolved_shop_url = shop_url
                r.resolution_status = "resolved"
                r.resolution_method = method
                resolved += 1
            elif shop_url == "conflict":
                r.resolution_status = "conflict"
                r.resolution_method = method
                pending += 0  # not pending — surfaced in /admin/conflicts
            else:
                # Spec: failed resolution → 'pending', NOT 'unresolvable'.
                # The graph keeps growing as more events arrive; the
                # reprocessor revisits these rows.
                r.resolution_status = "pending"
                r.resolution_method = method
                pending += 1

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.exception("wa.messages: outer commit failed")
        raise HTTPException(500, f"commit failed: {type(e).__name__}: {e}")

    logger.info(
        "wa.messages received=%d duplicates=%d resolved=%d pending=%d",
        len(items), duplicates, resolved, pending,
    )
    return WhatsAppRawMessagesResult(
        received=len(items),
        duplicates=duplicates,
        resolved=resolved,
        pending=pending,
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
    """Group lifecycle events. The bridge currently does not feed this
    endpoint, but it's wired so we can turn membership tracking on later
    without changing the intern's contract."""
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
