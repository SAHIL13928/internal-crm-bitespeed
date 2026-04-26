"""Load Frejun call records into the calls table.

The same per-record mapping (`apply_call_record`) is shared by:
  - the bulk loader entrypoints (`load_from_file`, `load_from_api`)
  - the live webhook receiver (`crm_app.webhooks.frejun`)

Bulk modes:
  1. Bulk export JSON. Pass `FREJUN_DUMP_PATH` env var or argv[1].
  2. Live API pull. GET https://api.frejun.com/api/v1/calls/  Auth: Token <FREJUN_API_KEY>.

Frejun call object fields we map (adjust here if Frejun's schema changes):
  uuid                str, unique call id
  call_type           "incoming" | "outgoing"
  call_status         "completed" | "missed" | "no-answer" | ...
  start_time          ISO8601
  duration            int seconds
  from_number         "+91..."
  to_number           "+91..."
  agent_email
  agent_name          (or `user_name`)
  recording_url       (or `recording`)
  transcript          (if Frejun's transcription add-on is enabled)
  call_summary        (or `summary`)
  sentiment

Run as:
    python -m etl.load_frejun                   # uses FREJUN_DUMP_PATH or live API
    python -m etl.load_frejun frejun_dump.json  # bulk file
"""
import json
import os
import sys
from datetime import datetime
from typing import Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session  # noqa: E402

from crm_app.db import Base, SessionLocal, engine  # noqa: E402
from crm_app.models import Call  # noqa: E402
from crm_app.utils import build_phone_to_shop, norm_phone  # noqa: E402


def _parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, AttributeError):
        return None


def apply_call_record(record: dict, db: Session, phone_to_shop: dict) -> Tuple[Optional[Call], bool, bool]:
    """Map one Frejun call record onto a Call row.

    Returns (call, is_new, matched_to_shop).
    Caller is responsible for the surrounding transaction (commit / savepoint).
    Raises ValueError on missing call id."""
    cid = record.get("uuid") or record.get("id")
    if not cid:
        raise ValueError("missing call id (uuid)")

    direction = "outbound" if (record.get("call_type") or "").lower().startswith("out") else "inbound"
    connected = (record.get("call_status") or "").lower() in {"completed", "answered", "connected"}
    from_n = record.get("from_number") or record.get("from")
    to_n = record.get("to_number") or record.get("to")
    counterparty = to_n if direction == "outbound" else from_n
    shop = phone_to_shop.get(norm_phone(counterparty))

    existing = db.get(Call, cid)
    if existing is None:
        call = Call(id=cid)
        db.add(call)
        is_new = True
    else:
        call = existing
        is_new = False

    call.shop_url = shop
    call.direction = direction
    call.connected = connected
    call.started_at = _parse_iso(record.get("start_time") or record.get("started_at"))
    call.duration_sec = record.get("duration") or record.get("call_duration")
    call.from_number = from_n
    call.to_number = to_n
    call.agent_email = record.get("agent_email")
    call.agent_name = record.get("agent_name") or record.get("user_name")
    call.recording_url = record.get("recording_url") or record.get("recording")
    call.transcript = record.get("transcript")
    call.summary = record.get("call_summary") or record.get("summary")
    call.sentiment = record.get("sentiment")
    call.raw = json.dumps(record, ensure_ascii=False)

    return call, is_new, shop is not None


def ingest_calls(records):
    """Bulk loader entry point — used by load_from_file / load_from_api / run_etl."""
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    inserted = updated = matched = skipped = 0
    try:
        phone_to_shop = build_phone_to_shop(db)
        for r in records:
            try:
                _, is_new, m = apply_call_record(r, db, phone_to_shop)
            except ValueError:
                skipped += 1
                continue
            if is_new:
                inserted += 1
            else:
                updated += 1
            if m:
                matched += 1
            if (inserted + updated) % 200 == 0 and (inserted + updated) > 0:
                db.commit()
        db.commit()
        print(f"calls inserted: {inserted}")
        print(f"calls updated:  {updated}")
        print(f"matched to shop:{matched}/{inserted + updated}")
        if skipped:
            print(f"skipped (no id):{skipped}")
    finally:
        db.close()


def load_from_file(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        records = data.get("results") or data.get("calls") or data.get("data") or []
    else:
        records = data
    print(f"Loaded {len(records)} call records from {path}")
    ingest_calls(records)


def load_from_api():
    import requests  # local to avoid import cost when not used
    api_key = os.environ.get("FREJUN_API_KEY")
    if not api_key:
        print("FREJUN_API_KEY not set; can't pull live. Skipping.")
        return
    url = "https://api.frejun.com/api/v1/calls/"
    headers = {"Authorization": f"Token {api_key}"}
    all_records = []
    next_url = url
    while next_url:
        resp = requests.get(next_url, headers=headers, timeout=30)
        resp.raise_for_status()
        body = resp.json()
        all_records.extend(body.get("results", []))
        next_url = body.get("next")
    print(f"Fetched {len(all_records)} calls from Frejun")
    ingest_calls(all_records)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        load_from_file(sys.argv[1])
    elif os.environ.get("FREJUN_DUMP_PATH"):
        load_from_file(os.environ["FREJUN_DUMP_PATH"])
    else:
        load_from_api()
