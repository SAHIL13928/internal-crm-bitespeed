"""End-to-end smoke test for the WhatsApp webhook receiver.

Mirrors the spec actually sent to the WA bridge intern:
required fields are group_name, sender_phone, sender_name, timestamp,
is_from_me, message_type, plus body or media_url. No message_id, no JID.

Run:
    python smoke_test_webhooks.py             # uses http://127.0.0.1:8765 by default

Server must be up:
    python -m uvicorn crm_app.main:app --port 8765

Exits 0 on full pass, 1 on first failure (with diff printed).
"""
import json
import os
import sys
import uuid

import requests
from dotenv import load_dotenv

load_dotenv()

BASE = os.environ.get("CRM_BASE_URL", "http://127.0.0.1:8765")
SECRET = os.environ.get("WHATSAPP_WEBHOOK_SECRET")
MSG_URL = f"{BASE}/webhooks/whatsapp/messages"
GRP_URL = f"{BASE}/webhooks/whatsapp/groups"
HEALTH_URL = f"{BASE}/api/health"

H_OK = {"Content-Type": "application/json", "X-Webhook-Secret": SECRET or ""}
H_BAD = {"Content-Type": "application/json", "X-Webhook-Secret": "wrong"}
H_NONE = {"Content-Type": "application/json"}

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"  ok   {name}")
    else:
        FAIL.append((name, detail))
        print(f"  FAIL {name}  {detail}")


def post(url, headers, body):
    return requests.post(url, headers=headers, data=json.dumps(body), timeout=10)


def main():
    if not SECRET:
        print("WHATSAPP_WEBHOOK_SECRET not set in env or .env — abort.")
        sys.exit(2)

    run = uuid.uuid4().hex[:8]
    GROUP_NAME = f"Smoke Test {run}"

    print(f"Base: {BASE}")
    print(f"Run id: {run}\n")

    # Minimal payload matching what the bridge will actually send
    def msg(ts, body, sender_phone="+919999999999", sender_name="Aditi", is_from_me=False, message_type="text", media_url=None):
        out = {
            "group_name": GROUP_NAME,
            "sender_phone": sender_phone,
            "sender_name": sender_name,
            "timestamp": ts,
            "is_from_me": is_from_me,
            "message_type": message_type,
        }
        if body is not None:
            out["body"] = body
        if media_url is not None:
            out["media_url"] = media_url
        return out

    print("[1] Auth")
    r = post(MSG_URL, H_NONE, msg("2026-04-26T10:00:00Z", "x"))
    check("missing secret -> 401", r.status_code == 401, f"got {r.status_code}")
    r = post(MSG_URL, H_BAD, msg("2026-04-26T10:00:00Z", "x"))
    check("wrong secret -> 401", r.status_code == 401, f"got {r.status_code}")

    print("\n[2] Single message — server derives message_id, dedupe on retry")
    body = msg("2026-04-26T10:00:00+00:00", "hello")
    r = post(MSG_URL, H_OK, body); j = r.json()
    check("insert -> 202", r.status_code == 202, f"got {r.status_code}")
    check("inserted=1 updated=0", j.get("inserted") == 1 and j.get("updated") == 0, json.dumps(j))
    accepted = j.get("accepted_ids", [])
    check("derived id present", len(accepted) == 1 and accepted[0].startswith("derived:"), json.dumps(j))
    derived_id_1 = accepted[0]

    # Replay identical payload -> same derived hash -> updated, not inserted
    r = post(MSG_URL, H_OK, body); j = r.json()
    check("replay -> updated=1 (no duplicate row)", j.get("updated") == 1 and j.get("inserted") == 0, json.dumps(j))
    check("same derived id on replay", j.get("accepted_ids") == [derived_id_1], json.dumps(j))

    print("\n[3] Bridge-supplied message_id wins over derived")
    explicit = msg("2026-04-26T10:00:30Z", "explicit id wins")
    explicit["message_id"] = f"bridge-{run}-1"
    r = post(MSG_URL, H_OK, explicit); j = r.json()
    check("explicit id used as-is", j.get("accepted_ids") == [f"bridge-{run}-1"], json.dumps(j))

    print("\n[4] Batch of two distinct messages")
    batch = {"messages": [
        msg("2026-04-26T10:01:00Z", "second"),
        msg("2026-04-26T10:02:00Z", "third"),
    ]}
    r = post(MSG_URL, H_OK, batch); j = r.json()
    check("batch -> 202", r.status_code == 202, f"got {r.status_code}")
    check("batch inserted=2", j.get("inserted") == 2, json.dumps(j))
    check("batch failed=0", len(j.get("failed", [])) == 0, json.dumps(j))

    print("\n[5] Document/media payload (body absent)")
    media = msg("2026-04-26T10:03:00Z", body=None, message_type="document",
                media_url="https://example.com/file.pdf")
    r = post(MSG_URL, H_OK, media); j = r.json()
    check("media insert -> 202", r.status_code == 202, f"got {r.status_code}")
    check("media inserted=1", j.get("inserted") == 1, json.dumps(j))

    print("\n[6] Validation errors -> 422 (sender should NOT retry)")
    r = post(MSG_URL, H_OK, {"group_name": "x"})  # missing required fields
    check("missing required -> 422", r.status_code == 422, f"got {r.status_code}: {r.text[:120]}")

    big_batch = {"messages": [msg("2026-04-26T10:00:00Z", f"x{i}") for i in range(1001)]}
    r = post(MSG_URL, H_OK, big_batch)
    check("oversize batch -> 422", r.status_code == 422, f"got {r.status_code}")

    print("\n[7] Group binding by group_name (no JID provided)")
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from crm_app.db import SessionLocal
    from crm_app.models import WhatsAppGroup, WhatsAppMessage
    db = SessionLocal()
    try:
        wag = db.query(WhatsAppGroup).filter_by(group_name=GROUP_NAME).one_or_none()
        check("WhatsAppGroup row created from group_name", wag is not None)
        check("WhatsAppGroup has no JID (intern doesn't send it)", wag is not None and wag.group_jid is None,
              f"got jid={wag.group_jid if wag else None}")
        check("WhatsAppGroup last_activity_at populated", wag is not None and wag.last_activity_at is not None)

        # 4 unique messages this run: hello, explicit-id one, second, third, plus 1 media = 5
        msgs = db.query(WhatsAppMessage).filter(WhatsAppMessage.group_name == GROUP_NAME).all()
        check("5 messages persisted", len(msgs) == 5, f"got {len(msgs)}")
        for m in msgs:
            check(f"msg {m.message_id[:30]}… timestamp is naive UTC",
                  m.timestamp is not None and m.timestamp.tzinfo is None,
                  f"tzinfo={m.timestamp.tzinfo if m.timestamp else None}")
    finally:
        db.close()

    print("\n[8] Group lifecycle endpoint (currently inactive but still wired)")
    r = post(GRP_URL, H_OK, {
        "event_type": "group_created",
        "group_id": f"smoke-{run}@g.us",
        "group_name": f"{GROUP_NAME} (jid carrier)",
        "members": [{"phone": "+919999999999", "name": "Aditi", "is_admin": True}],
        "changed_at": "2026-04-26T09:00:00Z",
    })
    check("group endpoint accepts -> 202", r.status_code == 202, f"got {r.status_code}")

    print("\n[9] Health endpoint")
    r = requests.get(HEALTH_URL, timeout=10); j = r.json()
    check("health 200", r.status_code == 200)
    wa = j.get("whatsapp", {})
    check("health.whatsapp.messages >= 5", wa.get("messages", 0) >= 5, json.dumps(wa))
    check("health.whatsapp.webhook_secret_configured true", wa.get("webhook_secret_configured") is True)
    check("health.whatsapp.last_message_received_at present", wa.get("last_message_received_at") is not None)

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for n, d in FAIL:
            print(f"  - {n}: {d}")
        sys.exit(1)


if __name__ == "__main__":
    main()
