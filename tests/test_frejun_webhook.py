"""Regression tests for the FreJun live-webhook field mapping.

Payloads are real samples copied verbatim from production Render logs
on 2026-04-27 — at that point the endpoint was returning 422 because
the extractor only recognized `id`/`uuid`, not the live `call_id`.
"""

SECRET = "frejun-test-secret"
PATH = "/webhooks/frejun/calls"


def _post(client, body, secret=SECRET):
    headers = {"X-Webhook-Secret": secret} if secret else {}
    return client.post(PATH, json=body, headers=headers)


# Real payload from Render logs — call.status event, inbound completed call
LIVE_STATUS_INBOUND = {
    "event": "call.status",
    "call_id": "X6Zq913",
    "start_time": "2026-04-27T17:22:22.545801+05:30",
    "call_creator": "vani.jayaram@bitespeed.co",
    "creator_number": "+918446080154",
    "candidate_number": "+919081660300",
    "candidate_name": None,
    "virtual_number": "+918645329622",
    "call_type": "inbound",
    "call_status": "Call completed",
    "org_identifier": "gjxE9gw",
    "metadata": {"reference_id": None, "job_id": None, "transaction_id": None},
    "answer_time": "2026-04-27T17:22:38.037000+05:30",
    "end_time": "2026-04-27T17:23:41.105356+05:30",
    "duration": 62960,  # MILLISECONDS — should store as 62 seconds
}

# call.status event, outbound — initiated, not yet connected
LIVE_STATUS_OUTBOUND_INITIATED = {
    "event": "call.status",
    "call_id": "je5zDw9",
    "start_time": "2026-04-27T17:23:41.057153+05:30",
    "call_creator": "harsh.pathak@bitespeed.co",
    "creator_number": "+919667281117",
    "candidate_number": "+919440312500",
    "candidate_name": None,
    "virtual_number": "+919240028207",
    "call_type": "outbound",
    "call_status": "Outbound call initiated",
    "org_identifier": "gjxE9gw",
    "metadata": {"reference_id": None, "job_id": None, "transaction_id": None},
}

# call.recording event — second event for a previously-seen call_id
LIVE_RECORDING = {
    "event": "call.recording",
    "call_id": "OQzeZoo",
    "start_time": "2026-04-27T17:23:13.273371+05:30",
    "call_creator": "aryan.dev@bitespeed.co",
    "creator_number": "+918826376828",
    "candidate_number": "+917045668514",
    "candidate_name": "mah",
    "virtual_number": "+919240028217",
    "call_type": "outbound",
    "recording_url": "https://api.frejun.com/api/v1/core/call-recordings/LkzA366?signature=_I06UuCmkERq9kZ5bWYZNZUcX8efcc8WlABdgW5jAiE",
    "summary_url": "https://product.frejun.com/interviews/shared-interview/jrwkrWW?signature=MH3EHNoYntkZsA9jXCiFvpNQNW4TufeDtXy0gx-tm5g",
    "org_identifier": "gjxE9gw",
    "metadata": {"reference_id": None, "job_id": None, "transaction_id": None},
}


def test_live_status_inbound_parsed(tmp_app):
    """The original 422 case — `call_id` instead of `id`/`uuid`."""
    client = tmp_app["client"]
    db_module = tmp_app["db_module"]
    from crm_app.models import Call

    r = _post(client, LIVE_STATUS_INBOUND)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["inserted"] == 1, body
    assert body["failed"] == [], body

    db = db_module.SessionLocal()
    try:
        call = db.get(Call, "X6Zq913")
        assert call is not None
        assert call.direction == "inbound"
        assert call.connected is True  # "Call completed" → connected
        # Duration 62960 ms → 62 seconds (we divide by 1000 for values >3600)
        assert call.duration_sec == 62
        assert call.agent_email == "vani.jayaram@bitespeed.co"
        # Inbound: from = candidate (the merchant), to = creator (BS user)
        assert call.from_number == "+919081660300"
        assert call.to_number == "+918446080154"
    finally:
        db.close()


def test_outbound_initiated_not_connected(tmp_app):
    """`call_status: "Outbound call initiated"` should NOT mark connected."""
    client = tmp_app["client"]
    db_module = tmp_app["db_module"]
    from crm_app.models import Call

    r = _post(client, LIVE_STATUS_OUTBOUND_INITIATED)
    assert r.status_code == 202, r.text
    db = db_module.SessionLocal()
    try:
        call = db.get(Call, "je5zDw9")
        assert call is not None
        assert call.direction == "outbound"
        assert call.connected is False
    finally:
        db.close()


def test_recording_event_does_not_blank_prior_fields(tmp_app):
    """call.status fires first with start_time/duration. call.recording
    fires later WITHOUT duration. The second event must preserve the
    original duration (additive update, not blanket overwrite)."""
    client = tmp_app["client"]
    db_module = tmp_app["db_module"]
    from crm_app.models import Call

    # 1. First event sets duration_sec via call.status
    status_first = {**LIVE_RECORDING}
    status_first["event"] = "call.status"
    status_first["call_status"] = "Call completed"
    status_first["duration"] = 45000  # 45 seconds in ms
    status_first.pop("recording_url", None)
    status_first.pop("summary_url", None)
    r = _post(client, status_first)
    assert r.status_code == 202

    # 2. Second event (recording) arrives without duration — must NOT
    #    blank duration_sec.
    r = _post(client, LIVE_RECORDING)
    assert r.status_code == 202

    db = db_module.SessionLocal()
    try:
        call = db.get(Call, "OQzeZoo")
        assert call is not None
        assert call.duration_sec == 45  # preserved from first event
        assert call.recording_url == LIVE_RECORDING["recording_url"]
        assert call.connected is True  # set by first event, kept by second
    finally:
        db.close()


def test_legacy_uuid_payload_still_works(tmp_app):
    """The v2 API backfill format uses `uuid` and lowercase status —
    must keep working alongside the new live-webhook format."""
    client = tmp_app["client"]
    legacy = {
        "uuid": "legacy-1",
        "call_type": "outgoing",
        "call_status": "completed",
        "start_time": "2026-04-26T11:00:00Z",
        "duration": 240,  # SECONDS in legacy format
        "creator_number": "+919000000000",
        "candidate_number": "+919999999999",
        "recruiter": "ops@bitespeed.co",
    }
    r = _post(client, legacy)
    assert r.status_code == 202, r.text
    db_module = tmp_app["db_module"]
    db = db_module.SessionLocal()
    try:
        from crm_app.models import Call
        call = db.get(Call, "legacy-1")
        assert call is not None
        assert call.direction == "outbound"  # "outgoing" → outbound
        assert call.connected is True
        assert call.duration_sec == 240  # legacy seconds preserved
        assert call.agent_email == "ops@bitespeed.co"
    finally:
        db.close()
