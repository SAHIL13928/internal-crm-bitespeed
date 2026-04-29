# Deploy to AWS

> Plain-English walkthrough for getting a public URL with populated
> data on AWS. ~1-1.5 hours the first time.

## What we're standing up

```
GitHub repo
    │ (App Runner pulls + builds Dockerfile)
    ▼
AWS App Runner service ──────► public HTTPS URL
    │
    │ DATABASE_URL env var
    ▼
Amazon RDS PostgreSQL  (db.t4g.micro, free tier)
    │
    └── tables auto-created by SQLAlchemy on first boot
        bootstrap_render.py reads CSVs from data/inputs/ → fills tables
```

## Why App Runner + RDS

- **App Runner** is the closest AWS equivalent to Render/Heroku: connect
  GitHub repo, it builds + deploys, you get HTTPS automatically.
- **RDS Postgres** is managed (no patching, automated backups, free tier
  for 12 months on db.t4g.micro).
- The `Dockerfile` in the repo handles both the Python backend and the
  TypeScript frontend in one build — App Runner picks it up automatically.

Other options if your org has preferences:
- **ECS Fargate + RDS** — same Dockerfile, more control, more setup
- **Elastic Beanstalk + RDS** — older, more clicking, supported
- **Lightsail** — single VPS, ssh-and-run style

## Step-by-step (App Runner + RDS)

### 1. Create the Postgres database

AWS Console → **RDS** → Create database → **Standard create**

- Engine: **PostgreSQL** (default version is fine)
- Templates: **Free tier**
- DB instance identifier: `cs-crm-db`
- Master username: `cs_crm_admin` (or anything)
- Master password: long random — **save this, you need it for DATABASE_URL**
- Instance class: `db.t4g.micro`
- Storage: 20 GiB (free tier max)
- Connectivity:
  - **Public access: Yes** (simplest for App Runner; lock down later via VPC connector)
  - VPC security group: create new
- Initial database name: `cs_crm`

Click **Create database**. Wait ~5-10 min until status = "Available".

Once available, click into the DB → note the **Endpoint** (looks like
`cs-crm-db.xxxxxxxx.us-east-1.rds.amazonaws.com`).

Your `DATABASE_URL` will be:
```
postgresql://cs_crm_admin:<password>@cs-crm-db.xxxxxxxx.us-east-1.rds.amazonaws.com:5432/cs_crm
```

### 2. Open the security group so App Runner can reach RDS

In RDS → click your DB → Connectivity & security → click the VPC
security group → Edit inbound rules → **Add rule**:

- Type: PostgreSQL (port 5432 auto-fills)
- Source: **Anywhere-IPv4** (`0.0.0.0/0`) — fine for demo, tighten later

Save. (Production-grade fix: use App Runner's VPC connector + private
RDS. Skip for now.)

### 3. Create the App Runner service

AWS Console → **App Runner** → **Create service**

- Source: **Source code repository**
- Connect to GitHub (one-time OAuth)
- Repository: pick this repo
- Branch: `main`
- Deployment trigger: **Automatic** (every push deploys)
- Configuration: **Use a configuration file** (it'll find `apprunner.yaml`
  + `Dockerfile`)

**Service settings:**
- Service name: `cs-crm`
- CPU: 1 vCPU (smallest)
- Memory: 2 GB
- Port: 8000 (matches Dockerfile)
- Health check path: `/api/health`

**Environment variables** (the important part — fill ALL of these):

| Name | Value |
|---|---|
| `DATABASE_URL` | the RDS connection string from step 1 |
| `API_USERNAME` | `bitespeed` (or anything) |
| `API_PASSWORD` | long random — `python -c "import secrets; print(secrets.token_urlsafe(24))"` |
| `ALLOWED_ORIGINS` | `https://<your-app-runner-url>` (you'll get this after creation; update later) |
| `WHATSAPP_WEBHOOK_SECRET` | yours |
| `PERISKOPE_SIGNING_SECRET` | yours (from Periskope console) |
| `FREJUN_WEBHOOK_SECRET` | yours |
| `ADMIN_SECRET` | long random |
| `PERISKOPE_API_KEY` | yours (for backfill — optional) |
| `PERISKOPE_PHONE` | the WA org number, no `+`, e.g. `917708751301` |
| `FREJUN_API_KEY` | yours (for backfill — optional) |

Click **Create & deploy**. App Runner builds the Dockerfile (~3-5 min
first time) and starts the container.

### 4. Watch the deploy

App Runner dashboard → click your service → **Logs** tab.

You should see:
```
[bootstrap] shops in DB: 0
[bootstrap] starting bootstrap …
[bootstrap] [1/5] loading shops + contacts + whatsapp_groups
…
[bootstrap] bootstrap complete in 60.0s
INFO: Application startup complete.
INFO: Uvicorn running on http://0.0.0.0:8000
```

Once you see "startup complete", the URL at the top of the App Runner
service page is live.

### 5. Smoke-test

```bash
# Public, no auth needed
curl https://<your-app-runner-url>.awsapprunner.com/api/health

# Protected — should 401 without auth, 200 with
curl -u 'bitespeed:<password>' \
  https://<your-app-runner-url>.awsapprunner.com/api/merchants?q=avishya

# Frontend — opens in browser, prompts for basic auth
open https://<your-app-runner-url>.awsapprunner.com/app/
```

### 6. Run the historical backfills (optional, one-shot)

In App Runner you don't get a Shell tab like Render. Two options:

**Option A — locally, against the RDS DB:**
```bash
DATABASE_URL='postgresql://...rds...' python scripts/backfill_periskope.py
DATABASE_URL='postgresql://...rds...' python scripts/backfill_frejun_calls.py
DATABASE_URL='postgresql://...rds...' python scripts/extract_meetlinks_from_messages.py
DATABASE_URL='postgresql://...rds...' python scripts/reprocess_pending.py
```

These scripts work the same on Postgres as on SQLite (we made the
`INSERT … ON CONFLICT` calls dialect-agnostic).

**Option B — via ECS one-off task** (more setup, scriptable). Skip
unless you're already on ECS.

## What's different vs. Render

| | Render | AWS App Runner |
|---|---|---|
| Source | `render.yaml` | `Dockerfile` (auto-detected) + `apprunner.yaml` |
| Database | Free Postgres add-on | Separate RDS Postgres instance |
| Auto-build | yes | yes |
| Sleep on idle | yes (free tier) | no (App Runner doesn't sleep) |
| Cold start | ~30s | none — always warm |
| Cost (low traffic) | $0 | ~$5-25/mo |
| Shell access | yes | no (use ECS exec or local DATABASE_URL) |

## Common gotchas

- **App Runner can't reach RDS** → security group not opened. Step 2.
- **`DATABASE_URL` rejects** `postgres://` prefix → app normalizes to
  `postgresql+psycopg2://` automatically; just paste the RDS string as-is.
- **First boot timeout** → bootstrap takes ~60s. App Runner's default
  health check timeout is generous; if it fails, increase to 90s in the
  service settings.
- **CORS errors in browser** → `ALLOWED_ORIGINS` env var doesn't match
  the actual frontend origin. Set it to the exact URL App Runner gave
  you, no trailing slash.
- **Build fails in npm step** → make sure `frontend/package-lock.json`
  is committed (it must be in git for `npm ci`).

## Local dev still works the same

```bash
python -m uvicorn crm_app.main:app --port 8765
# → uses SQLite at crm.db, no Postgres needed
```

The Dockerfile only kicks in for the AWS deploy. Local dev path is
unchanged.
