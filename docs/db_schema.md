# DB schema — internal CS-CRM

Postgres in prod; SQLite for local/test. Schema is dialect-clean: same
SQLAlchemy models, dialect-aware upserts via
`crm_app.db.insert_on_conflict_do_nothing`. SQL definitions live in
`crm_app/models.py`; this doc is the operator-side cheatsheet.

`shop_url` is the canonical merchant key, lower-cased on write. Every
other table joins through it.

---

## Core entities

### `shops` *(PK: shop_url)*

The merchant. One row per Shopify store URL.

| col              | type      | notes |
|------------------|-----------|-------|
| `shop_url`       | varchar PK | lowercase, e.g. `examplestore.myshopify.com` |
| `brand_name`     | varchar    | display name; populated by `etl/enrich_shops.py` from XLSX |
| `health_status`  | varchar    | `healthy` \| `at_risk` \| `dnc` \| `unknown` |
| `outreach_status`| varchar    | `open` \| `dnc` |
| `dnc_reason`     | varchar    | freeform when DNC |
| `dnc_note`       | text       | freeform |
| `dnc_revisit_on` | date       | optional revisit hint |
| `account_manager`| varchar    | internal owner |
| `confidence`     | varchar    | mapping confidence tier |
| `created_at` / `updated_at` | datetime | server time, naive UTC |

### `contacts` *(PK: id)*

Per-shop people: merchant-side and internal AMs both stored here.

| col          | type    | notes |
|--------------|---------|-------|
| `id`         | int PK  | autoincrement |
| `shop_url`   | varchar | FK → `shops.shop_url`, indexed |
| `name`       | varchar | |
| `email`      | varchar | indexed |
| `phone`      | varchar | indexed; raw, not normalized — use `crm_app.utils.norm_phone` to compare |
| `is_internal`| bool    | true ⇒ Bitespeed AM/CSM, not the merchant |
| `role`       | varchar | freeform job title |

### `whatsapp_groups` *(PK: id)*

Static directory of known WA groups for a shop. The `group_jid` is the
canonical Periskope group identifier; `group_name` is fuzzier (humans
rename groups).

| col                | type    | notes |
|--------------------|---------|-------|
| `id`               | int PK  | |
| `shop_url`         | varchar | FK → `shops.shop_url` (nullable for unbound groups) |
| `group_jid`        | varchar | indexed; from Periskope payloads |
| `group_name`       | varchar | display name |
| `last_activity_at` | datetime| updated by webhook ingestion |

---

## Communications

### `meetings` *(PK: id, Fireflies ULID)*

Fireflies-recorded meetings. Audio/video/transcript URLs come from the
Fireflies export.

Key cols: `shop_url`, `title`, `date` (indexed), `duration_min`,
`organizer_email`, `host_email`, `meeting_link`, `transcript_url`,
`audio_url`, `video_url`, `summary_short`, `summary_overview`,
`summary_bullet_gist`, `summary_keywords` (JSON-encoded list, stored as
TEXT for cross-DB portability), `action_items`, `mapping_source`
(`link` \| `email` \| `none`).

### `meeting_attendees` *(PK: id)*

Many-to-one to `meetings`. `email`, `display_name`, `is_internal`.
`is_internal` is set from the bitespeed.co domain check at load time.

### `calls` *(PK: id, Frejun call uuid)*

`shop_url`, `direction` (`inbound`|`outbound`), `connected`,
`started_at` (indexed), `duration_sec`, `from_number`, `to_number`,
`agent_email`, `agent_name`, `recording_url`, `transcript`, `summary`
(may be JSON string from FreJun ai_insights), `sentiment`
(`happy`|`neutral`|`concerned`|`frustrated`), `raw` (original payload).

The list endpoint enriches `summary` by parsing `ai_insights` JSON if
present; see `crm_app/main.py::_enrich_call_item`.

### `whatsapp_messages` *(PK: message_id)*

Older landing table. `whatsapp_raw_messages` (below) is the canonical
intake surface for the WA bridge — keep in mind both exist.

### `whatsapp_group_events` *(PK: id)*

Periskope group-membership / metadata events — joins, leaves, renames.
`event_type`, `group_id`, `members` (JSON list of
`{phone, name, is_admin}`), `changed_at`, `received_at`, `raw`.

### `whatsapp_raw_messages` *(PK: id)*

Canonical WA intake — the table the bridge POSTs into.

| col                  | type     | notes |
|----------------------|----------|-------|
| `group_name`         | varchar  | indexed, NOT NULL |
| `sender_phone`       | varchar  | indexed, NOT NULL, E.164 from intern |
| `sender_name`        | varchar  | optional |
| `timestamp`          | datetime | indexed, NOT NULL |
| `body`               | text     | NOT NULL, default `''` (SQLite NULLs are distinct, breaks UNIQUE) |
| `is_from_me`         | bool     | NOT NULL |
| `message_type`       | varchar  | `text` \| `document`, NOT NULL |
| `media_url`          | text     | |
| `source_message_id`  | varchar  | indexed; Periskope's stable id (used for edit/delete events — natural key dedupe alone breaks on edits because `body` changes) |
| `is_edited` / `edited_at` | bool / datetime | Periskope `message.updated` |
| `is_deleted` / `deleted_at` | bool / datetime | Periskope `message.deleted` |
| `received_at`        | datetime | server time |
| `processed_at`       | datetime | when resolution last ran |
| `resolution_status`  | varchar  | enum, see below |
| `resolved_shop_url`  | varchar  | FK → `shops.shop_url` (set when resolved) |
| `resolution_method`  | varchar  | `static_directory` \| `identity_graph` \| `manual` etc. |

Constraints:
- `UniqueConstraint(group_name, sender_phone, timestamp, body)` — natural-key
  idempotency for intern retries.
- `CHECK resolution_status IN ('pending','resolved','unresolvable','conflict')`.

#### `resolution_status` enum

| value          | meaning |
|----------------|---------|
| `pending`      | Just inserted, resolver hasn't run or didn't find a binding. The reprocessor revisits these. |
| `resolved`     | `resolved_shop_url` is set and trusted. |
| `unresolvable` | Tried the graph, no match. Manual binding needed. |
| `conflict`     | Identity graph BFS reached >1 distinct shop within depth 3. Surfaced via `/admin/conflicts`. |

---

## Issues + notes

### `issues` *(PK: id)*

`shop_url`, `title`, `description`, `priority`
(`high`|`med`|`low`), `status` (`open`|`in_progress`|`resolved`),
`source` (`whatsapp`|`call`|`meeting`|`manual`), `source_ref` (id of
related call/meeting), `owner`, `jira_ticket_id`, `opened_at`,
`resolved_at`. `resolved_at` is auto-stamped on PATCH when status
flips to `resolved`.

### `notes` *(PK: id)*

`shop_url`, `author`, `body` (text), `is_followup`, `due_at`,
`created_at`. Free-form per-shop notes; `is_followup=true` rows surface
as TODOs in the dashboard.

---

## Identity graph

Two tables, no joins to the rest of the app schema except
`evidence_table`/`evidence_id` (intentionally a soft pointer — we don't
want graph deletes to cascade into communications).

### `identities` *(PK: id)*

A typed node in the graph.

| col     | type    | notes |
|---------|---------|-------|
| `id`    | int PK  | |
| `kind`  | varchar | `shop_url` \| `phone` \| `email` \| `meeting_link` \| `group_name` |
| `value` | varchar | normalized per kind: `phone` → digits-only / Indian-last-10 (`crm_app.utils.norm_phone`); `email`/`shop_url` lowercased |

`UNIQUE (kind, value)`. Index on `(kind, value)`.

### `bindings` *(PK: id)*

An undirected edge between two identities, with provenance.

| col              | type     | notes |
|------------------|----------|-------|
| `identity_a_id` / `identity_b_id` | int FK | always stored with `a_id < b_id` so each edge appears exactly once (`CHECK identity_a_id < identity_b_id`) |
| `source`         | varchar  | `static_directory` \| `whatsapp` \| `frejun` \| `fireflies` \| `manual` |
| `confidence`     | float    | currently always `1.0`; retained for future weighting |
| `observed_at`    | datetime | when the co-occurrence was seen |
| `evidence_table` | varchar  | optional pointer (e.g. `whatsapp_raw_messages`) |
| `evidence_id`    | varchar  | row id within that table |

`UNIQUE (identity_a_id, identity_b_id, source, evidence_id)` — same
edge from the same evidence is a no-op.

### How phone resolves to shop_url

1. `phone` value comes off a webhook (FreJun call, Periskope WA message).
2. `crm_app.identity.resolve_shop_url_for(kind="phone", value=...)`
   normalizes the phone, looks up its `Identity`, then BFS with
   `depth ≤ DEFAULT_DEPTH (3)`.
3. Among nodes reached, count distinct `shop_url` identities:
   - 0 → `unresolvable`
   - 1 → that shop_url, with `resolution_method="identity_graph"`
   - 2+ → `conflict` (surfaced at `/admin/conflicts` and recorded on
     the WA row's `resolution_status`).
4. Edges are added by webhook handlers whenever they see a real
   co-occurrence — never from string fuzziness. New edges may unblock
   previously `pending` rows; the reprocessor walks pending rows on
   change.

Static directory bindings (the seed set) come from
`scripts/recompute_static_directory_bindings.py`, run during bootstrap.

---

## Bootstrap data flow

`scripts/bootstrap_render.py` runs at container start. It's idempotent:
if `shops` table is non-empty, it's a ~1s no-op. On a fresh DB:

1. **`etl/load_shops.py`** → reads
   `data/inputs/shopurl + number + emailids.csv` →
   populates `shops`, `contacts`, `whatsapp_groups`.
2. **`etl/enrich_shops.py`** → reads `data/inputs/Master_filled_v7.xlsx`
   (best-effort; skipped if missing) → fills `shops.brand_name`.
3. **`etl/load_finance_contacts.py`** → reads
   `data/inputs/Finance Contacts - Sheet1.csv` → adds finance
   contacts as `contacts` rows with `is_internal=false`.
4. **`etl/load_fireflies.py`** → reads
   `data/inputs/meetings_raw.json` and the Fireflies CSVs →
   populates `meetings` + `meeting_attendees`. `mapping_source` is
   `link` (matched a `meetlinkstoshopUrl` row), `email` (matched
   merchant-domain attendee), or `none`.
5. **`scripts/recompute_static_directory_bindings.py`** → seeds
   `identities` + `bindings` from the static directory so the very
   first webhook can resolve via the graph (and not just the in-memory
   phone_to_shop dict).

`calls`, `whatsapp_raw_messages`, `whatsapp_group_events`, and
`calendar_events` are populated **only via live webhooks / sync** — no
historical backfill is run at boot (would blow past Render's start
timeout). `scripts/backfill_*.py` exist for manual replay.

`run_etl.py` at repo root runs the same pipeline outside the container
for local-dev population of `crm.db`.

---

## Migrations

- SQLite path: `crm_app.main._run_migrations()` runs PRAGMA-based
  idempotent ALTERs at app start (covers the legacy
  `whatsapp_groups.group_jid` and `whatsapp_raw_messages.source_message_id`
  / edit-tracking columns).
- Postgres path: those migrations are **skipped** in `_run_migrations`
  (no PRAGMA in Postgres). Schema deltas live under `migrations/`
  (`0001_identity_and_raw_messages.sql`, `0002_calendar.sql`,
  `postgres_init.sql`) and are applied manually by the operator —
  there's no Alembic in the repo. For a fresh Postgres deploy
  `Base.metadata.create_all` (called from `crm_app/main.py`) creates
  all tables; only existing-DB schema deltas need the SQL files.
