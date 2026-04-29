# CS-CRM — Bitespeed merchant 360°

Internal customer-success CRM. Per-merchant view of meetings, calls,
WhatsApp threads, contacts, and KPIs. Powered by FreJun (calls),
Fireflies (meetings), and Periskope (WhatsApp).

```
                                                ┌──────────────────┐
   FreJun webhook ──────► /webhooks/frejun ───►│                  │
   Periskope webhook ───► /webhooks/periskope ►│  FastAPI backend │
   Periskope REST  ─────► scripts/backfill ───►│  + identity graph │──► Frontend (TS)
   Static CSVs ─────────► run_etl.py ─────────►│  on SQLite/Postgres │   /app/
                                                └──────────────────┘
```

## Quick start (local)

```bash
# 1. Activate venv + install deps
venv\Scripts\activate         # Windows
pip install -r requirements.txt

# 2. Make sure .env has the secrets (see docs/deploy.md for the list)
# 3. Build the DB from the committed input files
python scripts/bootstrap_render.py

# 4. Run the API
python -m uvicorn crm_app.main:app --port 8765

# 5. Open http://127.0.0.1:8765/app/  (basic auth — set API_USERNAME/PASSWORD in .env)
```

## Repo layout

```
crm_app/                     FastAPI backend
  main.py                    app, middleware, all read routes
  auth.py                    HTTP Basic dependency + middleware gate
  db.py                      SQLA engine + session
  models.py                  ORM (Shop, Contact, Meeting, Call,
                             WhatsAppRawMessage, Identity, Binding, …)
  schemas.py                 Pydantic request/response
  utils.py / time_utils.py   shared helpers
  resolver.py                per-event shop_url resolution
  identity.py                identity graph + BFS resolver
  admin.py                   /admin/conflicts (header-protected)
  webhooks/
    frejun.py                /webhooks/frejun/calls
    whatsapp.py              /webhooks/whatsapp/messages (intern path)
    periskope.py             /webhooks/periskope (HMAC-verified)

etl/                         Bulk loaders (CSV/JSON → DB)
scripts/                     One-off mapping & backfill tools (see scripts/README.md)
tests/                       pytest — webhooks, identity graph, ingestion
data/inputs/                 Committed seed data (CSVs, Fireflies dumps)
migrations/                  Hand-rolled SQL — kept in sync with ORM
docs/                        Long-form docs (deploy, integrations, archive)
frontend/                    TypeScript SPA (Vite + Tailwind via CDN)
render.yaml                  Render service config
```

## Identity graph — the core mapping insight

Each comms source uses a different key for "which merchant?":

- **FreJun** — phone number
- **Fireflies** — meeting link + attendee emails
- **Periskope** — group name + sender phone
- **Static directory** — operator-curated CSV

The fix is an **identity graph**: typed nodes (`shop_url`, `phone`,
`email`, `meeting_link`, `group_name`) connected by observed
co-occurrences. Connected components = same merchant. New evidence
retroactively resolves orphan rows. We never add edges from string
fuzziness — only from real co-occurrence.

See `crm_app/identity.py` and `crm_app/resolver.py`.

## Where to read next

- **Single EC2 box (Postgres + app via Docker Compose):** [docs/ec2.md](docs/ec2.md)
- **AWS App Runner + RDS:** [docs/aws.md](docs/aws.md)
- **Render (alternative):** [docs/deploy.md](docs/deploy.md)
- **Backend reference (endpoints, ETL stages):** [docs/backend.md](docs/backend.md)
- **Periskope native webhook (for the WA bridge intern):** [docs/whatsapp_periskope_native.md](docs/whatsapp_periskope_native.md)
- **Scripts catalog:** [scripts/README.md](scripts/README.md)

## Tests

```bash
python -m pytest tests/ -q
```

Covers all four webhooks (FreJun, WA intern, Periskope, admin),
identity-graph operations, edge cases for phone canonicalization, and
edit/delete event flows. **34 tests, all passing.**

## Status snapshot

|  | Coverage |
|---|---:|
| Shops in static directory | 1,683 |
| Contacts | 11,750 |
| WhatsApp chats imported | 4,346 |
| WhatsApp messages backfilled | 472,449 |
| WhatsApp messages resolved to a merchant | 365,614 (**77%**) |
| WA chats bound to a merchant | 4,282 (**78%**) |
| FreJun calls imported | 44,910 |
| Calls bound to a merchant | 6,113 (14%) |
| Fireflies meetings | 5,208 (incl. 166 upcoming extracted from WA invites) |
| Identity graph nodes / edges | 9,519 / 14,694 |
