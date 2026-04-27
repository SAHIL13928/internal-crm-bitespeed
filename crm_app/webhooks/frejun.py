"""Frejun call ingestion webhook.

Auth: shared secret in `X-Webhook-Secret`, constant-time compared against
`FREJUN_WEBHOOK_SECRET`. FreJun does not sign payloads — they let us configure
custom outgoing-webhook headers, so we authenticate by header.

Per-record mapping is shared with the bulk loader via
`etl.load_frejun.apply_call_record`.
"""
import hmac
import json
import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..utils import build_phone_to_shop

logger = logging.getLogger("crm.webhook.frejun")
router = APIRouter(prefix="/webhooks/frejun", tags=["webhooks"])

WEBHOOK_SECRET = os.environ.get("FREJUN_WEBHOOK_SECRET")


def _verify_secret(header_value: Optional[str]):
    if not WEBHOOK_SECRET:
        raise HTTPException(503, "FREJUN_WEBHOOK_SECRET not configured on server")
    if not header_value or not hmac.compare_digest(header_value, WEBHOOK_SECRET):
        raise HTTPException(401, "invalid webhook secret")


def _extract_records(payload):
    """FreJun sends one of several shapes:
      - bare call object (live webhook): `{event, call_id, ...}`
      - wrapped: `{event, data: {...}}`
      - bulk list (older format): `[{...}, {...}]`
    Recognize any of `id`, `uuid`, or `call_id` as the record marker."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
        # Bare event payload (the live webhook shape).
        if any(k in payload for k in ("uuid", "id", "call_id")):
            return [payload]
    return []


@router.post("/calls", status_code=status.HTTP_202_ACCEPTED)
async def receive_calls(
    request: Request,
    x_webhook_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    body = await request.body()
    _verify_secret(x_webhook_secret)

    # TODO REMOVE: temporary raw-payload capture so we can verify FreJun's actual
    # field names against the mapper. Delete this line once we've seen a sample.
    logger.info("frejun.calls.RAW_PAYLOAD: %s", body.decode("utf-8", errors="replace"))

    try:
        payload = json.loads(body or b"{}")
    except json.JSONDecodeError:
        raise HTTPException(400, "invalid JSON body")

    records = _extract_records(payload)
    if not records:
        raise HTTPException(422, "no call records in payload")

    # Late import to avoid pulling etl/ at module load
    from etl.load_frejun import apply_call_record

    phone_to_shop = build_phone_to_shop(db)
    inserted = updated = matched = 0
    failed: list = []
    accepted_ids: list = []

    for r in records:
        cid_pre = r.get("uuid") or r.get("id") or "<no-id>"
        try:
            with db.begin_nested():
                _, is_new, was_matched = apply_call_record(r, db, phone_to_shop)
            if is_new:
                inserted += 1
            else:
                updated += 1
            if was_matched:
                matched += 1
            accepted_ids.append(cid_pre)
        except Exception as e:
            logger.exception("frejun.calls: failed call_id=%s", cid_pre)
            failed.append({"call_id": cid_pre, "error": f"{type(e).__name__}: {e}"})

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.exception("frejun.calls: outer commit failed")
        raise HTTPException(500, f"commit failed: {type(e).__name__}: {e}")

    logger.info(
        "frejun.calls received=%d inserted=%d updated=%d matched=%d failed=%d",
        len(records), inserted, updated, matched, len(failed),
    )
    return {
        "received": len(records),
        "inserted": inserted,
        "updated": updated,
        "matched_to_shop": matched,
        "failed": failed,
        "accepted_ids": accepted_ids,
    }
