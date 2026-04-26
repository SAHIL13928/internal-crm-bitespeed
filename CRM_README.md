# Internal CS-CRM — Backend

A working FastAPI backend that powers the merchant-success workflows shown in
the [reference mockup](https://illustrious-gingersnap-d91c92.netlify.app/):
search a merchant, see their KPI strip, browse meetings + calls + WhatsApp
groups, file issues, drop notes, mark a merchant Do-Not-Contact.

No frontend yet. Hit it with `curl` / Postman / the auto-generated Swagger UI
at `/docs`.

## Stack
- **FastAPI** (REST)
- **SQLAlchemy** + **SQLite** (single file `crm.db` — easy to inspect)
- **Pydantic v2** schemas

## Layout

```
crm_app/
  db.py             # engine + session
  models.py         # SQLAlchemy ORM
  schemas.py        # Pydantic request/response
  utils.py          # shared helpers (phone normalize, UTC, phone->shop map)
  main.py           # API routes (merchants, meetings, calls, issues, notes, timeline, health)
  webhooks/
    whatsapp.py     # POST /webhooks/whatsapp/messages|groups (bearer secret)
    frejun.py       # POST /webhooks/frejun/calls (HMAC-SHA256 signature)
etl/
  load_shops.py     # data/inputs/shopurl + number + emailids.csv -> shops, contacts, whatsapp_groups
  load_fireflies.py # data/inputs/meetings_raw.json + ...                  -> meetings, attendees
  load_frejun.py    # bulk loader (JSON dump or live API) — shares apply_call_record() with the webhook
run_etl.py          # one-shot: `python run_etl.py [shops|fireflies|frejun|all]`
data/
  inputs/           # files the live ETL reads (Fireflies dumps, master CSV, WA-content CSV)
  scratch/          # legacy artifacts from scripts/ (review CSVs, xlsx exports)
scripts/            # one-off Fireflies fetchers + exploration utilities (not in hot path)
tests/
  smoke_test_50.py                # walks the API for 50 real merchants
  smoke_test_whatsapp_webhook.py  # 29 checks
  smoke_test_frejun_webhook.py    # 22 checks
logs/               # runtime logs (gitignored)
crm.db              # SQLite (created by ETL/app on first run)
```

## Setup

```bash
venv\Scripts\activate            # already created
pip install -r requirements.txt
```

## Loading data

```bash
python run_etl.py                # all stages (shops + fireflies + frejun)
python run_etl.py shops          # CSV only
python run_etl.py fireflies      # meetings only
python run_etl.py frejun calls.json   # calls from a Frejun export dump
```

Re-running is idempotent: `shops` wipes-and-reloads each merchant's children;
`fireflies` upserts meetings by id.

### What `fireflies` maps

For every Fireflies meeting we try to map it to a `shopUrl` in two passes:

1. **Link match** — the meeting's `meeting_link` (from
   `meetings_with_links.json`) is looked up in the Arindam meet-link → shop
   table built from `meetlinkstoshopUrl (1).csv`.
2. **Email match** — any *external* attendee email is checked against
   `emails_to_clients.csv`. First hit wins.

Unmapped meetings are still ingested with `shop_url = NULL` so they show up
in admin queries — they just don't surface on a merchant profile.

Current coverage on this dataset: **~40%** (2 038 / 5 042 meetings).

### Frejun

We don't have a Frejun export or API key in `.env` yet, so `load_frejun.py`
is **wired but inert**. To enable:

- Drop a Frejun export at any path and run `python run_etl.py frejun <path>`,
  **OR**
- Add `FREJUN_API_KEY=...` to `.env` and run `python run_etl.py frejun`.

The loader expects Frejun's `calls` shape (`uuid`, `call_type`,
`call_status`, `start_time`, `duration`, `from_number`, `to_number`,
`agent_email`, `recording_url`, optional `transcript`/`call_summary`/
`sentiment`). It maps each call to a shop by phone-number lookup against
`contacts.phone`. Adjust the field names in `etl/load_frejun.py` if the
export schema differs.

## Running the API

```bash
python -m uvicorn crm_app.main:app --reload --port 8765
```

Swagger UI: <http://127.0.0.1:8765/docs>

## Endpoints (mirrors the mockup)

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/health` | counts by table |
| GET | `/api/merchants?q=&health=&limit=&offset=` | search by shopUrl/brand |
| GET | `/api/merchants/{shopUrl}` | full profile + KPI strip + contacts + WA groups |
| GET | `/api/merchants/{shopUrl}/contacts` | |
| GET | `/api/merchants/{shopUrl}/whatsapp` | |
| POST | `/api/merchants/{shopUrl}/dnc` | mark DNC (`reason`, `note`, `revisit_on`) |
| GET | `/api/merchants/{shopUrl}/meetings?since=&until=&limit=` | |
| GET | `/api/meetings/{id}` | transcript URL, summary, action items, attendees |
| GET | `/api/merchants/{shopUrl}/calls?since=&until=&direction=` | |
| GET | `/api/calls/{id}` | recording URL, transcript, summary |
| GET | `/api/merchants/{shopUrl}/issues?status=` | |
| POST | `/api/merchants/{shopUrl}/issues` | create |
| PATCH | `/api/issues/{id}` | status/owner/jira_ticket_id |
| GET | `/api/merchants/{shopUrl}/notes` | |
| POST | `/api/merchants/{shopUrl}/notes` | create (with optional follow-up + due date) |
| GET | `/api/merchants/{shopUrl}/timeline?since=&until=` | unified meetings + calls + notes + issues, newest first |
| POST | `/webhooks/whatsapp/messages` | ingest one message or `{messages:[…≤1000]}` batch from the WA bridge — see "Webhooks" below |
| POST | `/webhooks/whatsapp/groups` | (inactive) group lifecycle endpoint — bridge currently does not feed this; kept wired for later |
| POST | `/webhooks/frejun/calls` | Frejun call event ingestion — HMAC-SHA256 signed body, see "Webhooks" below |

## Webhooks (WhatsApp ingestion)

The WA bridge (run by the other intern) POSTs JSON to
`/webhooks/whatsapp/messages`. We own dedupe, storage, and shop binding.

**Auth.** Shared secret in the `X-Webhook-Secret` header, compared in constant
time. Set `WHATSAPP_WEBHOOK_SECRET=<long-random>` in `.env`. Missing config → 503.
Wrong / missing secret → 401.

**Required payload fields** (matches the spec sent to the bridge):
`group_name`, `sender_phone` (E.164), `sender_name`, `timestamp`, `is_from_me`,
`message_type` (`text` | `document`), and `body` and/or `media_url`.

**Optional fields** the receiver will use if sent: `message_id`, `group_id`
(JID), `media_mime_type`, `media_caption`, `reply_to_message_id`, `is_edited`,
`is_deleted`, `raw`.

**Status semantics.** 202 on success (with per-row report), 401 on auth, 422 on
schema/oversize-batch (sender must NOT retry), 500 on commit failure (sender
SHOULD retry).

**Dedupe.** If the bridge sends `message_id`, that's the key. Otherwise the
server derives a stable SHA-256 fingerprint over
`(group_id, group_name, sender_phone, timestamp, message_type, body, media_url)`,
prefixed `derived:`. Retries of the identical payload upsert the same row.

**Per-row report.** Batch endpoint returns
`{received, inserted, updated, failed:[{message_id, error}], accepted_ids:[…]}`.
Each row commits in its own savepoint, so a single bad row never poisons the
batch.

**Side effects per message:**
- `WhatsAppGroup` row is found-or-created. Preference order:
  (a) match by `group_id` JID if sent, (b) match by `group_name` (single hit
  wins; ambiguous names log a warning and create a tracking row), (c) create new.
  `last_activity_at` is updated to the newest message timestamp.
- `whatsapp_messages.shop_url` is auto-resolved from the group's `shop_url`
  first, then falls back to matching `sender_phone` against `contacts.phone`
  (digits-only normalize).
- Timestamps are normalized to naive UTC for storage (matches rest of codebase).

**Health.** `GET /api/health` includes a `whatsapp` block with message count,
mapped-shop coverage, group counts, last-received timestamps, and whether the
secret is configured.

**Smoke test.** `python tests/smoke_test_whatsapp_webhook.py` exercises auth,
derived-id dedupe, explicit-id passthrough, batch, media payload, validation,
oversize batch, group_name binding, and health.

### Frejun (calls)

`POST /webhooks/frejun/calls`. Lives at `crm_app/webhooks/frejun.py`. Per-record
mapping is shared with the bulk loader via `etl.load_frejun.apply_call_record`.

**Auth.** FreJun does not sign payloads — they let us configure custom outgoing
webhook headers. We authenticate with a shared secret in `X-Webhook-Secret`,
constant-time compared against `FREJUN_WEBHOOK_SECRET`. Set the same value in
FreJun's webhook header config when subscribing to events.

**Payload tolerance.** Accepts (a) a bare call object, (b) `{event, data: {...}}`,
or (c) a list of call objects. Records without `uuid` or `id` are reported in
the `failed` array — the rest of the batch still commits (per-row savepoints).

**Shop binding.** Each call is mapped to a shop by digits-only match of the
counterparty number (`to_number` for outbound, `from_number` for inbound)
against `contacts.phone`.

**Backfill paths still exist:**
- `python -m etl.load_frejun <dump.json>` — bulk file
- `FREJUN_API_KEY=…  python -m etl.load_frejun` — paginated live pull
Both flows reuse the same `apply_call_record` mapping the webhook uses, so
behavior is consistent across ingest paths.

**Smoke test.** `python tests/smoke_test_frejun_webhook.py` covers signature
verification, dedupe, wrapped + bare + list payloads, per-row failure isolation,
direction inference, validation, and health.

## Known gaps / open questions

- **Frejun**: needs API key or a dump file. Until then, `calls` table is empty
  and the calls endpoints return `[]`.
- **WhatsApp message bodies**: we only have group *membership*, not message
  content. The reference mockup's WhatsApp tab (per-thread summaries,
  unresolved/resolved status, AI state summary) needs the actual messages.
  When we get them, model them as `whatsapp_messages` keyed by group + add a
  `whatsapp_threads` rollup.
- **`brand_name`**: not populated. Master sheet
  (`Fireflies Mapping (1) - ENRICHED.xlsx`) has the human brand names — easy
  pull-in but not yet wired.
- **Health status / AI state summary**: no automated rules yet — every
  merchant defaults to `unknown`. Will need a rules engine or a periodic LLM
  pass over recent comms.
