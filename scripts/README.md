# scripts/

One-shot Python tools. Each script lives at the root of `scripts/` and
walks `sys.path` up to the repo root, so invoke from anywhere as
`python scripts/<name>.py`.

Legacy / abandoned scripts moved to `scripts/_archive/` for git history
preservation; do not depend on them.

## Categorized index

### Boot / runtime
- **`bootstrap_render.py`** — runs in Render's `startCommand` before
  `uvicorn`. Rebuilds `crm.db` from committed inputs when the
  ephemeral disk is empty (fast no-op on warm restart).

### Backfill from external APIs
- **`backfill_frejun_calls.py`** — paginates `GET https://api.frejun.com/api/v2/integrations/calls/`
  and runs each historical call through the same mapper the live
  webhook uses (`etl.load_frejun.apply_call_record`). Rate-limited.
- **`backfill_periskope.py`** — paginates Periskope's REST API for
  chats + messages. Bulk-inserts into `whatsapp_raw_messages`.
  Resume-after-crash via `--start-offset`.

### Mapping / resolution passes (run AFTER data lands)
- **`recompute_static_directory_bindings.py`** — re-seed identity
  graph from the curated CSVs (phones, emails, group names → shop_url).
  Idempotent. Run after editing the master CSV.
- **`bind_chats_by_brand_name.py`** — for any unbound `WhatsAppGroup`
  whose `group_name` contains a merchant brand-name token, bind it.
  Strict tokens only (≥4 chars, stop-words filtered).
- **`extract_meetlinks_from_messages.py`** — regex Google Meet links
  out of WA message bodies, look up in the curated link→shop CSV,
  bind whole chats. Big mapping multiplier.
- **`extract_upcoming_meetings_from_wa.py`** — parse calendar invites
  pasted in WA, create future-dated `Meeting` rows so the dashboard's
  upcoming-meetings KPI is non-zero.
- **`enrich_from_new_sources.py`** — pulls in the team-curated
  `Master_filled_v7.xlsx` + `Fireflies Mapping - Sheet1.csv`:
  brand names, group_name→shop pairs, fuzzy meeting-title matches.
- **`backfill_call_shop_bindings.py`** — walks orphan FreJun calls
  and re-resolves them via the now-fatter identity graph. Run after
  any of the mapping passes above.
- **`reprocess_pending.py`** — walks `whatsapp_raw_messages` rows
  with `resolution_status='pending'` and re-runs the resolver. Use
  after the graph grows (e.g. after a binding pass).

## Suggested order on a fresh deploy

```
1. python run_etl.py                                     # static directory + Fireflies
2. python scripts/backfill_frejun_calls.py               # historical calls
3. python scripts/backfill_periskope.py                  # historical WA
4. python scripts/recompute_static_directory_bindings.py # seed graph
5. python scripts/enrich_from_new_sources.py             # team-curated extras
6. python scripts/extract_meetlinks_from_messages.py     # WA → meet → shop
7. python scripts/extract_upcoming_meetings_from_wa.py   # WA invites → upcoming
8. python scripts/reprocess_pending.py                   # resolve remaining WA
9. python scripts/backfill_call_shop_bindings.py         # rebind orphan calls
```

Steps 1-3 are heavy (network/data); 4-9 are pure compute and run in
seconds-to-minutes against the local DB.

## Safety knobs

Every mapping/binding script supports `--dry-run` (computes everything
then rolls back). Always prefer dry-run on production data.
