"""End-to-end smoke test for the Frejun call-ingestion webhook.

Auth model: shared secret in `X-Webhook-Secret` header (FreJun does not sign
payloads — they let us configure the header on outgoing webhooks).

Run:
    python tests/smoke_test_frejun_webhook.py    # uses http://127.0.0.1:8765 by default

Server must be up:
    python -m uvicorn crm_app.main:app --port 8765

Exits 0 on full pass, 1 on first failure.
"""
import json
import os
import sys
import uuid

import requests
from dotenv import load_dotenv

load_dotenv()

BASE = os.environ.get("CRM_BASE_URL", "http://127.0.0.1:8765")
SECRET = os.environ.get("FREJUN_WEBHOOK_SECRET")
URL = f"{BASE}/webhooks/frejun/calls"
HEALTH_URL = f"{BASE}/api/health"

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"  ok   {name}")
    else:
        FAIL.append((name, detail))
        print(f"  FAIL {name}  {detail}")


def post(body, secret=None):
    raw = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if secret is not None:
        headers["X-Webhook-Secret"] = secret
    return requests.post(URL, data=raw, headers=headers, timeout=10)


def main():
    if not SECRET:
        print("FREJUN_WEBHOOK_SECRET not set in env or .env — abort.")
        sys.exit(2)

    run = uuid.uuid4().hex[:8]
    print(f"Base: {BASE}")
    print(f"Run id: {run}\n")

    def call(uid, **overrides):
        out = {
            "uuid": uid,
            "call_type": "outgoing",
            "call_status": "completed",
            "start_time": "2026-04-26T11:00:00Z",
            "duration": 240,
            "from_number": "+919000000000",
            "to_number": "+919999999999",
            "agent_email": "ops@bitespeed.co",
            "agent_name": "Priya",
            "recording_url": "https://example.com/r.mp3",
            "call_summary": "Discussed pricing.",
            "sentiment": "neutral",
        }
        out.update(overrides)
        return out

    print("[1] Auth (shared-secret header)")
    r = post(call(f"c-{run}-1"))
    check("missing header -> 401", r.status_code == 401, f"got {r.status_code}")
    r = post(call(f"c-{run}-1"), secret="not-the-secret")
    check("wrong secret -> 401", r.status_code == 401, f"got {r.status_code}")

    print("\n[2] Single bare call object — accepted, dedupe on retry")
    body = call(f"c-{run}-1")
    r = post(body, secret=SECRET); j = r.json()
    check("insert -> 202", r.status_code == 202, f"got {r.status_code}")
    check("inserted=1", j.get("inserted") == 1, json.dumps(j))
    check("accepted_ids contains uuid", body["uuid"] in j.get("accepted_ids", []), json.dumps(j))

    r = post(body, secret=SECRET); j = r.json()
    check("dedupe -> updated=1", j.get("updated") == 1 and j.get("inserted") == 0, json.dumps(j))

    print("\n[3] Wrapped event {event, data: {...}}")
    body = {"event": "call.completed", "data": call(f"c-{run}-2", call_status="missed")}
    r = post(body, secret=SECRET); j = r.json()
    check("wrapped insert -> 202", r.status_code == 202, f"got {r.status_code}")
    check("wrapped inserted=1", j.get("inserted") == 1, json.dumps(j))

    print("\n[4] Batch (list of call objects)")
    batch = [call(f"c-{run}-3"), call(f"c-{run}-4", call_type="incoming")]
    r = post(batch, secret=SECRET); j = r.json()
    check("batch -> 202", r.status_code == 202, f"got {r.status_code}")
    check("batch inserted=2", j.get("inserted") == 2, json.dumps(j))

    print("\n[5] Per-row failure isolation (one valid + one missing uuid)")
    bad_then_good = [{"call_type": "outgoing", "from_number": "+91x"},   # no uuid
                     call(f"c-{run}-5")]
    r = post(bad_then_good, secret=SECRET); j = r.json()
    check("partial -> 202", r.status_code == 202, f"got {r.status_code}")
    check("good row inserted", j.get("inserted") == 1, json.dumps(j))
    check("bad row reported in failed", len(j.get("failed", [])) == 1, json.dumps(j))

    print("\n[6] Validation")
    r = post({}, secret=SECRET)
    check("empty payload -> 422", r.status_code == 422, f"got {r.status_code}")

    print("\n[7] Direction + shop binding inferred from numbers")
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from crm_app.db import SessionLocal
    from crm_app.models import Call
    db = SessionLocal()
    try:
        c = db.get(Call, f"c-{run}-1")
        check("call_1 persisted", c is not None)
        check("call_1 direction=outbound", c is not None and c.direction == "outbound",
              f"got {c.direction if c else None}")
        check("call_1 connected=True (status=completed)", c is not None and c.connected is True)
        c4 = db.get(Call, f"c-{run}-4")
        check("call_4 direction=inbound", c4 is not None and c4.direction == "inbound",
              f"got {c4.direction if c4 else None}")
    finally:
        db.close()

    print("\n[8] Health endpoint reflects Frejun config")
    r = requests.get(HEALTH_URL, timeout=10); j = r.json()
    check("health 200", r.status_code == 200)
    fr = j.get("frejun", {})
    check("frejun.webhook_secret_configured true", fr.get("webhook_secret_configured") is True, json.dumps(fr))
    check("calls counter > 0", j.get("calls", 0) > 0, json.dumps({"calls": j.get("calls")}))

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for n, d in FAIL:
            print(f"  - {n}: {d}")
        sys.exit(1)


if __name__ == "__main__":
    main()
