# Session report — 2026-04-27

Identity graph + WhatsApp raw-message ingestion.

## What shipped

### Phase B — WhatsApp raw ingestion (intern's contract)

- **New table `whatsapp_raw_messages`** (`crm_app/models.py:WhatsAppRawMessage`)
  with the intern's exact field set, plus server-side bookkeeping
  (`received_at`, `processed_at`, `resolution_status`,
  `resolved_shop_url`, `resolution_method`). Unique on
  `(group_name, sender_phone, timestamp, body)` for retry idempotency.
  CHECK constraint on `resolution_status`.
- **Rewrote `POST /webhooks/whatsapp/messages`**
  (`crm_app/webhooks/whatsapp.py`) to match the intern spec:
  - Auth via `X-Webhook-Secret` (`hmac.compare_digest`)
  - Pydantic validation; batch ≤500 (422 above; 413 also wired
    defensively)
  - Bulk `INSERT … ON CONFLICT DO NOTHING` via SQLite dialect, returning
    inserted ids so we can count duplicates without a re-query
  - 200 with `{received, duplicates, resolved, pending}` (the spec said
    "200 with {received, duplicates}" — I added `resolved`/`pending` so
    the bridge can log how many were bound inline; both fields are
    additive and don't break the spec contract)
  - 401 / 422 / 413 / 5xx per the retry policy in the intern doc
- **Inline resolution** on insert via `crm_app/resolver.py`. Failed →
  `pending` (NOT `unresolvable`, per spec).
- **Migration `migrations/0001_identity_and_raw_messages.sql`** — generated
  by hand from the ORM, NOT auto-applied. The app boots with
  `Base.metadata.create_all()` which creates the tables idempotently,
  so applying the SQL file is optional / for human review.
- **`docs/whatsapp_ingestion_for_intern.md`** — production URL, header,
  schema with examples, batch behavior, idempotency note, retry policy,
  sample curl.

### Phase C — Identity graph

- **Tables `identities` + `bindings`** with the constraints from spec
  (`UNIQUE(kind, value)`; bindings stored undirected via
  `CHECK(identity_a_id < identity_b_id)`; `UNIQUE(a_id, b_id, source,
  evidence_id)` to keep `add_binding` idempotent).
- **`crm_app/identity.py`** with:
  - `add_binding(...)` — idempotent; auto-creates identities; supplies a
    deterministic `evidence_id` stand-in when callers don't provide one
    (otherwise SQLite's "NULL is distinct" rule would let dupes through)
  - `resolve_shop_url_for(kind, value)` — BFS depth ≤3, returns
    highest-confidence shop_url, the sentinel `CONFLICT` if multiple,
    or `None`
  - `find_conflicts(...)` — drives `/admin/conflicts`
  - Per-kind value normalization at the API boundary (phones digits-only,
    emails + shop_urls lowercased), so `"+91 999-999-9999"` and
    `"+919999999999"` collapse to one identity
- **Wired into both webhooks** via `crm_app/resolver.py`:
  - `resolve_whatsapp_message(...)`: static (phone+group) → identity
    graph; grows the graph on success (idempotent edges)
  - `resolve_call(...)`: static phone → identity graph; same growth
  - `etl/load_frejun.apply_call_record` falls back to graph after the
    static dict misses, and adds a `phone↔shop_url` binding on success
    so backfill data populates the graph too
- **Seeded the production graph from the static directory**:
  ```
  identities: 8,320  (4,831 phone, 1,668 shop_url, 1,104 group_name, 717 email)
  bindings:   8,646
  ```
  (Run via `scripts/recompute_static_directory_bindings.py`. Idempotent.)
- **`scripts/reprocess_pending.py`** — walks pending raw messages and
  re-runs resolution. Idempotent. Stays `pending` if still unresolvable
  (does NOT downgrade to `unresolvable`, per spec).

### Phase D — Polish

- **`GET /admin/conflicts`** (and `/api/admin/conflicts` alias) —
  header-protected with `ADMIN_SECRET`. Returns identity-graph conflicts
  plus any raw messages whose resolver returned `static_directory_conflict`.
- **`scripts/recompute_static_directory_bindings.py`** — re-seed bindings
  from `shops/contacts/whatsapp_groups`. Has `--dry-run`.
- **README.md** — architecture, env-var matrix, runbook (how to
  investigate a wrong shop_url, how to manually add a binding, how to
  rebuild seeds, how to reprocess pending).
- **`/api/health`** now reports counts for `whatsapp_raw_messages`
  (total / resolved / pending / conflict) and identity graph
  (`identities`, `bindings`).
- **`render.yaml`** got `WHATSAPP_WEBHOOK_SECRET` + `ADMIN_SECRET` with
  `sync: false`.

## Tests

```
$ python -m pytest tests/test_whatsapp_ingestion.py tests/test_identity_graph.py
16 passed in ~15s
```

Coverage:

- WA: single insert, batch, dedupe (text + media-only), bad secret,
  oversize batch (422), missing fields (422), inline static-directory
  resolution, "stays pending not unresolvable" rule
- Identity: `add_binding` idempotency, self-edge skip, two-hop
  resolution, conflict detection, phone normalization, reprocessor
  picks up pending after a new binding, `/admin/conflicts` 401 + 200

The pytest harness uses a per-test SQLite file under `tmp_path` and a
fresh app import (see `tests/conftest.py`). The two existing
`smoke_test_*.py` scripts still run against a live server.

## Files changed / added

```
A  crm_app/identity.py
A  crm_app/resolver.py
M  crm_app/models.py            (+WhatsAppRawMessage, Identity, Binding)
M  crm_app/schemas.py           (+WhatsAppRawMessageIn/Batch/Result)
M  crm_app/webhooks/whatsapp.py (replaced /messages handler)
M  crm_app/main.py              (health additions, /admin/conflicts alias)
M  crm_app/admin.py             (+conflicts endpoint, ADMIN_SECRET guard)
M  etl/load_frejun.py           (graph fallback + edge-on-success)
M  render.yaml                  (+2 env vars)
M  .env                         (local dev values for the 2 new secrets)
M  README.md                    (rewrote — was a stale scraper README)

A  migrations/0001_identity_and_raw_messages.sql
A  docs/whatsapp_ingestion_for_intern.md

A  scripts/recompute_static_directory_bindings.py
A  scripts/reprocess_pending.py

A  tests/conftest.py
A  tests/test_whatsapp_ingestion.py
A  tests/test_identity_graph.py
```

## New env vars (values below — print once)

> Generated with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
> Saved into `.env` for local dev. **Set these in Render's dashboard
> (sync:false in render.yaml means Render won't auto-sync them).**

```
WHATSAPP_WEBHOOK_SECRET=lo3steiPE8UG9ofjDbqpmVBhfcJwEnmuv0Aq4ny9964
ADMIN_SECRET=hH2YAmdxTnOmLo_kbrsFGcAK6Y48G2m2SfcB6g6Y9Rg
```

(`FREJUN_WEBHOOK_SECRET` is unchanged — already set.)

After setting them in Render, hand `WHATSAPP_WEBHOOK_SECRET` to the WA
bridge intern via the doc at `docs/whatsapp_ingestion_for_intern.md`.

## Tradeoffs and judgment calls

1. **Replaced (not added alongside) the old `/webhooks/whatsapp/messages`
   handler.** The previous handler used a different table
   (`whatsapp_messages`), a different field set (with optional
   `message_id`/JID/etc.), and 202-with-detailed-failed-array semantics.
   The intern's actual contract is simpler. Keeping two handlers at the
   same path was not viable; layering both into one would have been
   confusing. The old `whatsapp_messages` ORM model + table are
   preserved for backward compatibility (the health endpoint still
   reports them) but the webhook no longer writes to them.
2. **`body` coerced to `""` server-side when null.** Spec said
   `UNIQUE(group_name, sender_phone, timestamp, body)`. SQLite treats
   NULLs as distinct in UNIQUE constraints, which would let media-only
   messages duplicate. Coercing to `""` keeps the spec's natural key
   meaningful. Documented in the intern doc and in code comments.
3. **Response shape includes `resolved`/`pending` in addition to spec's
   `received`/`duplicates`.** Additive — non-breaking. Lets the bridge
   log resolution metrics without an extra round trip.
4. **Migration is generated, not auto-applied.** I created
   `migrations/0001_*.sql` for human review and kept `create_all()` in
   `main.py` as the actual schema mechanism (consistent with how the
   repo already handles schema). When this codebase outgrows SQLite,
   the migration file is the head start for Alembic.
5. **`add_binding` always supplies a deterministic `evidence_id`
   stand-in when the caller passes `None`.** Without it, two
   "static_directory" seeds for the same `(a, b, source)` would create
   two rows because SQLite considers `NULL == NULL` to be false in
   UNIQUE.
6. **Confidence:** static-directory bindings are 1.0; observed
   co-occurrences from webhooks are 0.9 (real but slightly less
   trustworthy than the curated CSV). The BFS multiplies confidences
   along the path so distant nodes naturally rank lower.
7. **Bounded BFS at depth 3.** Real co-occurrence chains shouldn't need
   more, and capping prevents one accidental edge in a dense subgraph
   from rewriting resolution for distant nodes.
8. **Existing `WhatsAppMessage` table left in place.** It's referenced
   in `health` and by the old smoke test. Could be deprecated once the
   bridge has been on the new path for a while; not this session's
   scope.

## Open TODOs / next-session priorities

1. **Backfill `calls.shop_url` and `whatsapp_messages.shop_url` via
   the graph.** A one-shot script that walks every `Call` (or every
   message) with `shop_url IS NULL`, runs `resolve_shop_url_for`, and
   writes the result. Easy now that the graph is populated.
2. **Surface graph-derived bindings on the merchant profile.** The
   frontend mockup shows "1683 shops" — useful to show "this merchant
   has these aliases: phones X/Y, emails Z, group names …" via the
   identity component.
3. **Fix the static-directory conflicts.** A handful of phones are
   listed under multiple merchants in the seed CSV (sampled
   `/admin/conflicts` after seeding — most look like account-manager
   phones copied into many merchant rows). Either de-dupe the CSV or
   tag account-manager phones with `is_internal=true` so they're
   excluded from binding seeding.
4. **`whatsapp_messages` legacy deprecation.** Once the intern is on
   the new path for ≥2 weeks of real traffic, drop the old table,
   remove the `WhatsAppMessage` ORM, and clean up `health` references.
5. **Wire meeting_link → graph.** Fireflies' link-based mapping is
   already a (meeting_link, shop_url) co-occurrence — `add_binding`
   it during `etl/load_fireflies.py`.
6. **Performance:** `find_conflicts` is O(N · BFS). Fine at 8k
   identities, will need batching at ~100k.
7. **Test against PostgreSQL.** Current code uses
   `sqlalchemy.dialects.sqlite.insert` directly. When/if the database
   moves to Postgres, switch to a dialect-aware import or a small
   helper.

## Blocked

Nothing.

## Smoke commands for the next session

```bash
# Confirm graph is alive
python -m pytest tests/test_whatsapp_ingestion.py tests/test_identity_graph.py -v

# Re-seed (idempotent)
python scripts/recompute_static_directory_bindings.py

# Walk pending after any graph update
python scripts/reprocess_pending.py
```
