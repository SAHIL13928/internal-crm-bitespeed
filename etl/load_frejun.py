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


def _stringify(v):
    """SQLite TEXT columns can't bind Python list/dict — JSON-encode if structured."""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return v


def apply_call_record(record: dict, db: Session, phone_to_shop: dict) -> Tuple[Optional[Call], bool, bool]:
    """Map one Frejun call record onto a Call row.

    Field names match FreJun v2 (`/api/v2/integrations/calls/`):
      - id                  numeric primary id (used as our PK; fallback to `uuid` for legacy)
      - call_type           "outgoing" | "incoming"
      - status              "completed" | "answered" | "not-answered" | "missed" | ...
      - call_start_time     ISO8601 (no tz)
      - call_duration       seconds
      - creator_number      the Bitespeed user's phone (agent side)
      - candidate_number    the merchant's phone (counterparty — used for shop binding)
      - virtual_number      the FreJun virtual line used for the call
      - recruiter           the Bitespeed user's email
      - recording_url
      - call_transcript
      - ai_insights         (may be a structured object — JSON-encoded if so)

    Backward-compat fallbacks are kept for the older field names in case some
    payload ever differs.

    Returns (call, is_new, matched_to_shop). Caller owns the transaction.
    Raises ValueError on missing call id."""
    # `call_id` is the live-webhook field name; `id`/`uuid` come from the
    # v2 API backfill. Accept all three.
    cid = record.get("id") or record.get("uuid") or record.get("call_id")
    if cid is None:
        raise ValueError("missing call id")
    cid = str(cid)

    direction = "outbound" if (record.get("call_type") or "").lower().startswith("out") else "inbound"
    # Live webhook sends sentence-case statuses ("Call completed",
    # "Outbound call initiated"). Normalize and look for "completed" or
    # "answered" anywhere in the string. "initiated" / "ringing" are NOT
    # connected — those events fire before the candidate picks up.
    raw_status = (record.get("status") or record.get("call_status") or "").lower()
    connected = any(tok in raw_status for tok in ("completed", "answered", "connected")) and \
        not any(tok in raw_status for tok in ("initiated", "ringing", "missed", "not-answered", "not answered"))

    creator = record.get("creator_number")
    candidate = record.get("candidate_number")
    if direction == "outbound":
        from_n = creator or record.get("from_number") or record.get("from")
        to_n = candidate or record.get("to_number") or record.get("to")
    else:
        from_n = candidate or record.get("from_number") or record.get("from")
        to_n = creator or record.get("virtual_number") or record.get("to_number") or record.get("to")

    # Counterparty = merchant. For both directions that's `candidate_number`,
    # falling back to whichever side isn't ours.
    counterparty = candidate or (to_n if direction == "outbound" else from_n)
    shop = phone_to_shop.get(norm_phone(counterparty))

    # Identity-graph fallback when the static directory misses. Imported
    # lazily so etl tooling can run without the graph tables existing yet
    # (they're created by the app on boot via Base.metadata.create_all).
    if shop is None and counterparty:
        try:
            from crm_app import identity as _identity  # noqa: WPS433
            graph_result = _identity.resolve_shop_url_for(db, "phone", counterparty)
            if graph_result and graph_result != _identity.CONFLICT:
                shop = graph_result
        except Exception:  # noqa: BLE001 — never let identity lookup break ingestion
            pass

    summary = _stringify(record.get("ai_insights")) or record.get("call_summary") or record.get("summary")
    transcript = _stringify(record.get("call_transcript") or record.get("transcript"))

    existing = db.get(Call, cid)
    if existing is None:
        call = Call(id=cid)
        db.add(call)
        is_new = True
    else:
        call = existing
        is_new = False

    # Live webhook duration is in MILLISECONDS (e.g. 62960 ms ≈ 63 s).
    # The v2 API backfill sends seconds. Detect by magnitude — anything
    # over an hour expressed in seconds (3600s) is almost certainly ms.
    raw_dur = record.get("call_duration") or record.get("duration")
    duration_sec: Optional[int] = None
    if raw_dur is not None:
        try:
            d = int(raw_dur)
            duration_sec = d // 1000 if d > 3600 else d
        except (TypeError, ValueError):
            duration_sec = None

    # Agent email field varies by source: live webhook = `call_creator`,
    # v2 API = `recruiter`, older formats = `agent_email`.
    agent_email = (
        record.get("call_creator")
        or record.get("recruiter")
        or record.get("agent_email")
    )

    # Two events arrive per call (call.status, call.recording). We must
    # be additive on the second event: don't overwrite a populated field
    # with None just because this event didn't include it. Always-set
    # fields (like direction, connected) are fine to overwrite.
    def _set_if(field: str, value):
        if value not in (None, ""):
            setattr(call, field, value)

    # Always-current fields (latest event wins):
    call.shop_url = shop or call.shop_url
    call.direction = direction
    if connected or call.connected is None:
        call.connected = connected

    _set_if("started_at", _parse_iso(
        record.get("call_start_time") or record.get("start_time") or record.get("started_at")
    ))
    _set_if("duration_sec", duration_sec)
    _set_if("from_number", from_n)
    _set_if("to_number", to_n)
    _set_if("agent_email", agent_email)
    _set_if("agent_name", record.get("agent_name") or record.get("user_name") or record.get("candidate_name"))
    _set_if("recording_url", record.get("recording_url") or record.get("recording"))
    _set_if("transcript", transcript)
    _set_if("summary", summary or record.get("summary_url"))  # summary_url is FreJun's hosted summary page
    _set_if("sentiment", record.get("sentiment"))
    # raw always reflects the latest event so we can replay if needed.
    call.raw = json.dumps(record, ensure_ascii=False)

    # Grow the identity graph from this co-occurrence so future events can
    # resolve via the graph (idempotent — add_binding skips dupes).
    if shop and counterparty:
        try:
            from crm_app import identity as _identity  # noqa: WPS433
            _identity.add_binding(
                db,
                "phone", counterparty,
                "shop_url", shop,
                source="frejun",
                confidence=0.9,
                evidence_table="calls",
                evidence_id=cid,
            )
        except Exception:  # noqa: BLE001
            pass

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
