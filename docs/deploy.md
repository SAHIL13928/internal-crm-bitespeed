# Deploy to Render — production

> Single-path guide. Free tier works for low traffic; data persists
> in Render's managed Postgres so redeploys don't wipe state.

## Architecture

```
GitHub repo  ─push──►  Render web service  (FastAPI + uvicorn)
                          │
                          ├──► Render managed Postgres   (cs-crm-db)
                          ├──► /app/ (built TypeScript SPA, basic-auth-gated)
                          ├──► /api/* (basic-auth-gated)
                          ├──► /admin/conflicts (X-Admin-Secret-gated)
                          └──► /webhooks/* (per-provider secret-gated)
                                   ▲
   FreJun ────────POST────────────┤  /webhooks/frejun/calls
   Periskope ─────POST────────────┤  /webhooks/periskope
   WA bridge ─────POST────────────┘  /webhooks/whatsapp/messages
```

## Steps

1. **Stage + commit** the changeset:
   ```bash
   git add .gitignore render.yaml requirements.txt \
           README.md docs/ migrations/ \
           crm_app/ etl/ scripts/ tests/ \
           frontend/index.html frontend/package.json frontend/tsconfig.json \
           frontend/vite.config.ts frontend/.gitignore frontend/src/ \
           frontend/package-lock.json \
           data/inputs/
   git status        # eyeball before committing
   git commit -m "production: auth, postgres, ts frontend"
   git push origin main
   ```

   > Sanity-check the `git status` before committing — `.env`,
   > `crm.db`, `venv/`, `node_modules/`, `frontend/dist/` should all be
   > absent (they're in `.gitignore`).

2. **In Render dashboard** → your service → Settings → Environment, set:

   | Var | Value | Notes |
   |---|---|---|
   | `API_USERNAME` | `bitespeed` (or anything) | dashboard login |
   | `API_PASSWORD` | long random | `python -c "import secrets; print(secrets.token_urlsafe(24))"` |
   | `ALLOWED_ORIGINS` | `https://internal-crm-bitespeed.onrender.com` (+any Vercel etc.) | comma-sep |
   | `WHATSAPP_WEBHOOK_SECRET` | your value | intern bridge auth |
   | `PERISKOPE_SIGNING_SECRET` | from Arindam's Periskope console | HMAC verification |
   | `FREJUN_WEBHOOK_SECRET` | your value | FreJun custom-header auth |
   | `ADMIN_SECRET` | long random | `/admin/conflicts` |
   | `FREJUN_API_KEY` | (optional) | for backfill scripts |
   | `PERISKOPE_API_KEY`, `PERISKOPE_PHONE` | (optional) | for backfill scripts |
   | `DATABASE_URL` | **set automatically by Render** via `fromDatabase` in render.yaml | |

   Delete `FREJUN_CLIENT_ID` / `FREJUN_CLIENT_SECRET` if present —
   they're unused.

3. **Render auto-creates the Postgres database** the first time you
   deploy this `render.yaml` (the `databases:` block). Wait for the
   green "Available" indicator on the database service before the web
   service finishes building — they share `DATABASE_URL`.

4. **Watch the deploy logs** (Logs tab on the web service):
   ```
   ==> Building...
   pip install -r requirements.txt    (~30s)
   cd frontend && npm ci && npm run build    (~30s)
   ==> Starting...
   python scripts/bootstrap_render.py
       → reads DATABASE_URL → connects to Postgres
       → if shops table empty: runs ETL chain (~60s)
   uvicorn crm_app.main:app …
   ```

   First boot takes ~3 minutes total. Subsequent redeploys: ~90s
   (bootstrap is a no-op since Postgres data persists).

5. **Smoke-test** from your laptop:
   ```bash
   # public, no auth needed
   curl https://internal-crm-bitespeed.onrender.com/api/health

   # protected — should 401 without auth, 200 with
   curl -u 'bitespeed:<password>' \
        https://internal-crm-bitespeed.onrender.com/api/merchants?q=avishya

   # frontend
   open https://internal-crm-bitespeed.onrender.com/app/
   ```

   Browser will pop a basic-auth prompt on first visit — enter your
   `API_USERNAME` / `API_PASSWORD`.

## Cold-start behavior

- Render free-tier web services sleep after 15 min idle.
- First request after sleep: ~30s to wake uvicorn.
- The DB doesn't sleep (separate Postgres service).
- Bootstrap is a no-op on Postgres after the first run — no re-ETL on wake.

## Local development

```bash
# 1. Run the API (uses SQLite at crm.db)
python -m uvicorn crm_app.main:app --port 8765

# 2. In another terminal: hot-reload frontend
cd frontend
npm install     # one time
npm run dev     # serves at http://127.0.0.1:5173 with API proxy
```

The Vite dev server proxies `/api/*`, `/admin/*`, `/webhooks/*` to
uvicorn so basic-auth + same-origin all work as on production.

## Rollback

Render → Deploys → pick a green deploy → Redeploy. Or
`git revert <sha> && git push`. Postgres state is preserved across
rollbacks (rollback only re-runs migration on schema-changing commits).

## Backfilling history (one-shot)

Run inside Render's Shell tab on the web service:

```bash
# FreJun call history (requires FREJUN_API_KEY env var)
python scripts/backfill_frejun_calls.py

# Periskope WA history (requires PERISKOPE_API_KEY + PERISKOPE_PHONE)
python scripts/backfill_periskope.py

# After backfill: enrich + re-resolve
python scripts/recompute_static_directory_bindings.py
python scripts/enrich_from_new_sources.py
python scripts/extract_meetlinks_from_messages.py
python scripts/extract_upcoming_meetings_from_wa.py
python scripts/reprocess_pending.py
python scripts/backfill_call_shop_bindings.py
```

Periskope backfill takes ~5-10 minutes on real data. Each step is
idempotent — safe to re-run.

## Pre-demo checklist

- [ ] `git push` succeeded
- [ ] Render dashboard shows "Live" green checkmark on latest commit
- [ ] `curl /api/health` → non-zero shops/contacts/calls
- [ ] `curl -u user:pass /api/merchants?q=avishya` → returns rows
- [ ] Frontend `/app/` loads, search returns rows
- [ ] Warmed up (one request in the last few minutes — free tier sleeps at 15m idle)
- [ ] **Don't share the URL beyond your manager** — basic auth keeps casual visitors out, but the password is shared
