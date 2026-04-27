# Deploy to Render — testing/demo for the manager

> Single-path guide. Uses your existing free-tier Render service. No
> ngrok, no Vercel, no upgrades.

## How it works now

Render free tier has **no persistent disk** — `crm.db` gets wiped on
every cold start. Fix: we now **commit the input data files** (~22 MB
total) and run a bootstrap script at startup that rebuilds `crm.db`
from those inputs. Result: the app self-heals from a fresh filesystem
in ~60 seconds.

What's wired up:

- `.gitignore` no longer excludes `data/inputs/*` (only `data/scratch/`
  stays out).
- `scripts/bootstrap_render.py` checks if shops table is empty; if so,
  runs `load_shops → enrich_shops → load_finance_contacts → load_fireflies →
  recompute_static_directory_bindings`. Idempotent — exits in <1s on a
  warm restart.
- `render.yaml` `startCommand` runs the bootstrap before uvicorn:
  ```
  python scripts/bootstrap_render.py && uvicorn crm_app.main:app --host 0.0.0.0 --port $PORT
  ```
- The frontend (`frontend/index.html`) auto-detects same-origin so it
  works at `https://internal-crm-bitespeed.onrender.com/app/` with no
  config.

## Steps to deploy

1. **Stage the new files**:
   ```bash
   git add .gitignore render.yaml DEPLOY.md README.md \
           crm_app/ etl/ scripts/ tests/ migrations/ docs/ frontend/
   git add data/inputs/
   git status   # sanity-check before committing
   ```

   Sanity-check: there should be no `crm.db`, no `.env`, no `venv/`,
   no `data/scratch/` in the staged list. If any of those show up, fix
   `.gitignore` first.

2. **Commit + push** to whichever branch Render is tracking (probably
   `main`):
   ```bash
   git commit -m "deploy: identity graph + WA ingestion + bootstrap"
   git push origin main
   ```

3. **Set the env vars on Render** (Settings → Environment) — Render
   doesn't auto-sync these because `render.yaml` has `sync: false`:
   - `WHATSAPP_WEBHOOK_SECRET = lo3steiPE8UG9ofjDbqpmVBhfcJwEnmuv0Aq4ny9964`
   - `ADMIN_SECRET = hH2YAmdxTnOmLo_kbrsFGcAK6Y48G2m2SfcB6g6Y9Rg`
   - (`FREJUN_WEBHOOK_SECRET`, `FREJUN_API_KEY` should already be set
     from a prior session.)
   - `FREJUN_CLIENT_ID` / `FREJUN_CLIENT_SECRET` are unused — safe to
     leave unset or delete.

4. **Watch the deploy logs** (Render dashboard → Logs). On cold start
   you'll see the bootstrap output:
   ```
   [bootstrap] shops in DB: 0
   [bootstrap] starting bootstrap ...
   [bootstrap] [1/4] loading shops + contacts + whatsapp_groups
   ...
   [bootstrap] bootstrap complete in 60.0s
   ```
   First request after this completes will be served by uvicorn.

5. **Smoke test** from your laptop:
   ```bash
   curl https://internal-crm-bitespeed.onrender.com/api/health
   ```
   Expect `shops > 1600`, `meetings > 5000`, identity_graph counts
   non-zero. (Calls will be 0 — see "What's missing on Render" below.)

6. **Open the frontend**:
   ```
   https://internal-crm-bitespeed.onrender.com/app/
   ```
   Search a real merchant, browse contacts, meetings, timeline. This
   is the link you send your manager.

## What's missing on Render vs. your local DB

- **No FreJun call history**. Backfilling 44k calls via API takes
  too long for the bind window, so we skip it on bootstrap. Calls will
  populate going forward via the live webhook (already configured in
  FreJun's dashboard pointing at this URL).
- **No WhatsApp messages yet** — bridge intern is still wiring up.

If you want call history on Render specifically for the demo, run the
backfill **after** the deploy is up via Render's Shell:
```
python scripts/backfill_frejun_calls.py
```
Takes ~10 min. The result lives on Render's ephemeral disk — survives
until the next cold start, then gets wiped (so you'd need to re-run).

## Cold-start behaviour for the manager demo

- Render free tier sleeps after ~15 min idle.
- Wake-up cold start: ~30s to start uvicorn + ~60s for the bootstrap
  if `crm.db` is empty (it usually is post-sleep on free tier).
- **5 minutes before sending the link**, hit `/api/health` once to
  warm the service. Otherwise the manager's first click takes ~90s
  and they think it's broken.

## Pre-demo checklist

- [ ] `git status` clean (or only the files you intended)
- [ ] `git push` succeeded
- [ ] Render dashboard shows "Live" with a green checkmark on the
      latest commit
- [ ] `/api/health` returns non-zero shops/contacts/meetings
- [ ] Frontend `/app/` loads, search returns rows
- [ ] Warmed up (one request in the last few minutes)
- [ ] **Don't share the URL beyond your manager** — the API has no auth

## Rollback

If something breaks after the push, on Render dashboard → Deploys → pick
a prior green deploy → "Redeploy". Or `git revert <bad-sha> && git push`.
The bootstrap will regenerate the DB from whatever inputs are at that
revision.

## File-size sanity

The committed data files total ~22 MB:

| file                                      | size  |
|-------------------------------------------|-------|
| data/inputs/meetings_raw.json             | 15 MB |
| data/inputs/meetlinkstoshopUrl (1).csv    | 4.9 MB |
| data/inputs/meetings_with_links.json      | 2.7 MB |
| data/inputs/shopurl + number + emailids.csv | 416 KB |
| data/inputs/emails_to_clients.csv         | 80 KB |
| data/inputs/Finance Contacts - Sheet1.csv | 40 KB |

Well under GitHub's per-file 100 MB hard cap and the per-push warning
threshold. If new sources push us past ~80 MB total, switch to Git LFS
or a small data bucket the bootstrap downloads from.
