# CS-CRM — identity-graph runbook

> The full backend reference lives in **CRM_README.md** (endpoints, ETL,
> setup). This README focuses on the identity-graph layer + WhatsApp raw
> ingestion added in this iteration.

## Why this exists

Each comms source uses a different key for "which merchant is this?":

- **FreJun** — phone number
- **Fireflies** — meeting link + attendee emails
- **WhatsApp bridge** — group name + sender phone

Fuzzy-matching merchant *names* across sources is unreliable. Instead we
build an **identity graph**:

- **Nodes** are typed: `shop_url`, `phone`, `email`, `meeting_link`, `group_name`.
- **Edges** (bindings) are observed co-occurrences in real events
  (a phone seen on a call to a merchant; a group name seen in a WA message
  whose sender phone is in our static directory; etc.).
- A **connected component** is one merchant.
- Adding a single edge can retroactively resolve hundreds of orphan rows.

We never add an edge from string fuzziness — only from co-occurrence
in real events (or seeded from the static directory the operations team
maintains by hand).

## Architecture at a glance

```
                    ┌───────────────────────────────┐
   WA bridge        │  POST /webhooks/whatsapp/     │
   intern    ─────► │  messages                     │
                    │                               │
   FreJun           │  POST /webhooks/frejun/calls  │
   webhook   ─────► │                               │
                    │                               │
   FreJun     ────► │  scripts/backfill_frejun_     │
   backfill         │  calls.py                     │
                    └────────┬──────────────────────┘
                             │
                             ▼
              ┌─────────────────────────────┐
              │ crm_app/resolver.py         │
              │  • static directory match   │
              │  • identity-graph BFS       │
              │  • grow graph on resolve    │
              └────────┬────────────────────┘
                       │
            ┌──────────┴──────────┐
            ▼                     ▼
    whatsapp_raw_messages    identities + bindings
    calls.shop_url           (the graph)
```

Source files:

| File                              | Role                                                |
|-----------------------------------|-----------------------------------------------------|
| `crm_app/models.py`               | ORM (incl. `WhatsAppRawMessage`, `Identity`, `Binding`) |
| `crm_app/identity.py`             | `add_binding`, `resolve_shop_url_for`, `find_conflicts` |
| `crm_app/resolver.py`             | Per-event resolution used by webhooks (static → graph) |
| `crm_app/webhooks/whatsapp.py`    | `/messages` + `/groups` handlers                    |
| `crm_app/webhooks/frejun.py`      | `/calls` handler                                    |
| `crm_app/admin.py`                | `/api/admin/conflicts` and orphan-coverage tools     |
| `migrations/0001_*.sql`           | Hand-rolled DDL — kept in sync with ORM as the canonical schema doc |
| `scripts/recompute_static_directory_bindings.py` | Re-seed graph from `shops`     |
| `scripts/reprocess_pending.py`    | Re-run resolution on `pending` raw messages         |
| `tests/test_whatsapp_ingestion.py`, `tests/test_identity_graph.py` | pytest |

## Runbook

### "This call/message is bound to the wrong merchant — why?"

1. Pull the offending row's `shop_url` and the `counterparty_phone` (or
   `sender_phone` / `group_name`).
2. Ask the graph to explain itself:
   ```bash
   python -c "
   from crm_app.db import SessionLocal
   from crm_app.identity import resolve_shop_url_for, _shop_urls_in_component
   db = SessionLocal()
   print('resolves to:', resolve_shop_url_for(db, 'phone', '+919xxx'))
   print('component:  ', _shop_urls_in_component(db, 'phone', '+919xxx'))
   "
   ```
   If it returns `'conflict'` you'll see >1 shop in the component — that's
   the actual issue (bad data in the static directory, usually the same
   phone listed under two merchants).
3. Hit `/api/admin/conflicts` (or `/admin/conflicts`) with the
   `X-Admin-Secret` header to see the operator-friendly conflict list.

### "I want to manually pin this phone to this shop"

```python
from crm_app.db import SessionLocal
from crm_app.identity import add_binding
db = SessionLocal()
add_binding(
    db,
    "phone", "+919999999999",
    "shop_url", "merchant.myshopify.com",
    source="manual",
    confidence=1.0,
    evidence_table="manual",
    evidence_id="ticket-2026-04-27-A",   # something stable & traceable
)
db.commit()
```

The new edge takes effect for the next event. To bind already-pending WA
messages, run `python scripts/reprocess_pending.py`.

### "I edited the master shops CSV — how do I rebuild graph seeds?"

```bash
python run_etl.py shops                              # reload shops/contacts/groups
python scripts/recompute_static_directory_bindings.py
```

Both are idempotent.

### Reprocess pending WA messages after a graph update

```bash
python scripts/reprocess_pending.py
```

Walks every `whatsapp_raw_messages` row with `resolution_status='pending'`
and re-runs resolution. Newly resolved rows transition to `'resolved'`;
still-unresolved rows stay `'pending'` (we never downgrade to
`'unresolvable'` — new bindings may arrive later).

## Environment variables

| Var                          | Required | Purpose                              |
|------------------------------|----------|--------------------------------------|
| `WHATSAPP_WEBHOOK_SECRET`    | yes      | Bridge auth on `/webhooks/whatsapp/messages` |
| `FREJUN_WEBHOOK_SECRET`      | yes      | FreJun auth on `/webhooks/frejun/calls`      |
| `ADMIN_SECRET`               | yes      | Header auth for `/admin/conflicts`           |
| `FREJUN_API_KEY`             | for backfill | Used by `scripts/backfill_frejun_calls.py` and `etl/load_frejun.py` |
| `FIREFLIES_API_KEY`          | for ETL  | Fireflies meeting fetcher            |
| `CRM_DB_PATH`                | optional | Override SQLite path (used by tests) |

## Tests

```bash
python -m pytest tests/test_whatsapp_ingestion.py tests/test_identity_graph.py -v
```

The two existing **smoke** scripts (`tests/smoke_test_*.py`) still run
against a live server and exercise the broader API surface; they remain
useful for production smoke-testing but are not part of the pytest run.

## Status of the static-directory seed (snapshot 2026-04-27)

```
identities: ~8,300
bindings:   ~8,600
```

Conflicts present in the seed are surfaced at `/admin/conflicts` —
typically Bitespeed account-manager phone numbers that got copied into
multiple merchants' contact lists during the original ETL. Resolving them
is a data-quality task for the ops team, not a code fix.
