# Deploy on a single EC2 box (operator runbook)

Single Ubuntu host. Postgres + the FastAPI app run as Docker containers
on the same machine. nginx on the host proxies port 80 → app:8000.
Senior takes over from here for DNS + SSL.

```
                                ┌─ EC2 (ubuntu) ───────────────────┐
public traffic ──:80──► nginx ──┤  127.0.0.1:8000 → docker app    │
                                │                  └─ postgres    │
                                │                     (no host pt)│
                                └──────────────────────────────────┘
```

EC2 used in this walkthrough:
`ec2-34-239-140-115.compute-1.amazonaws.com`

---

## Step 1 — Local prep (your laptop, Windows)

The PEM file (`internalcrm.pem`) lives wherever you saved it. Lock its
permissions or SSH refuses to use it.

PowerShell:
```powershell
icacls .\internalcrm.pem /inheritance:r
icacls .\internalcrm.pem /grant:r "$($env:USERNAME):(R)"
```

Git Bash / WSL:
```bash
chmod 600 ./internalcrm.pem
```

Test SSH:
```bash
ssh -i ./internalcrm.pem ubuntu@ec2-34-239-140-115.compute-1.amazonaws.com
# If that fails with "permission denied (publickey)", try ec2-user:
ssh -i ./internalcrm.pem ec2-user@ec2-34-239-140-115.compute-1.amazonaws.com
```

**Security group** (AWS Console → EC2 → instance → Security tab → SG →
Edit inbound rules) — only two ports need to be open:

| Port | Source     | Why                              |
|------|------------|----------------------------------|
| 22   | your IP    | SSH (already open)               |
| 80   | 0.0.0.0/0  | nginx → public HTTP              |

Port 8000 stays **closed**. The app container is bound to the host's
loopback (`127.0.0.1:8000`) so even if 8000 were open in the SG, the
app wouldn't be reachable directly. nginx is the only way in.

If port 80 isn't open in the SG, ping Arindam:

> Bhai SG mein port 80 inbound bhi khol do please, nginx uspe expose hoga

---

## Step 2 — Code on the box

SSH in, then:

```bash
sudo apt-get update
sudo apt-get install -y git docker.io docker-compose-plugin nginx
sudo systemctl enable docker
sudo systemctl start docker
sudo usermod -aG docker $USER
```

> **Critical — read this:** the `usermod -aG docker` change does not
> take effect in your current shell. You **must** log out and SSH back
> in (or `exec su -l $USER`) before running any `docker` command.
> Otherwise you get `permission denied` on the docker socket and waste
> 20 minutes wondering why. This trips everyone up at least once.

After re-logging in, sanity check:

```bash
docker --version
docker compose version
docker ps   # should print an empty table, not an error
```

Then clone:

```bash
git clone https://github.com/SAHIL13928/internal-crm-bitespeed.git \
    ~/internal-crm-bitespeed
cd ~/internal-crm-bitespeed
```

If the repo is private, set up a GitHub deploy key on the box first
(or clone with HTTPS + a personal access token — either works).

---

## Step 3 — Create `.env` on the box

```bash
cp .env.example .env
nano .env
```

Fill in every value. Required vs. optional:

| Variable                       | Required? | Notes |
|--------------------------------|-----------|-------|
| `POSTGRES_DB`                  | required  | Leave default `cs_crm` unless you have a reason. |
| `POSTGRES_USER`                | required  | Leave default `cs_crm`. |
| `POSTGRES_PASSWORD`            | required  | Long random string — see below. |
| `API_USERNAME`                 | required  | Default `bitespeed`. |
| `API_PASSWORD`                 | required  | Long random — basic-auth gate on the dashboard + read API. |
| `ALLOWED_ORIGINS`              | required  | Set to `http://ec2-34-239-140-115.compute-1.amazonaws.com`. Comma-separated, no trailing slash. |
| `ADMIN_SECRET`                 | required  | Long random. Guards `/admin/conflicts` AND signs the OAuth session cookie. |
| `WHATSAPP_WEBHOOK_SECRET`      | optional  | Only needed if WA webhook is wired up. |
| `PERISKOPE_SIGNING_SECRET`     | optional  | Only needed for Periskope webhook. |
| `FREJUN_WEBHOOK_SECRET`        | optional  | Only needed for FreJun webhook. |
| `PERISKOPE_API_KEY` / `PERISKOPE_PHONE` | optional | Only for manual backfill scripts. |
| `FREJUN_API_KEY`               | optional  | Only for manual backfill scripts. |
| `GOOGLE_CLIENT_ID` / `_SECRET` / `_REDIRECT_URI` / `GOOGLE_SERVICE_ACCOUNT_JSON` / `GOOGLE_WORKSPACE_DOMAIN` | optional | Calendar sync. App boots without these; calendar endpoints 503 until configured. |
| `CALENDAR_TOKEN_ENCRYPTION_KEY`| optional  | Fernet key, only needed if Calendar sync is on. |

**Fresh secrets generated this session — paste these directly:**

```
POSTGRES_PASSWORD=PBxJh3ZbAsOxZYv5uoMZrCLB6HFHkbxq
API_PASSWORD=pH81FmwP3EGA-qjRJbBBj3GlTZ9k4Uj8
ADMIN_SECRET=hARbf0W9hs6Dotoa21j_XeMw_lVZ5mkfUHhXsFx4Wyk
```

Save (`Ctrl-O`, `Enter`, `Ctrl-X`). Lock it down:

```bash
chmod 600 .env
```

---

## Step 4 — Start the stack

```bash
docker compose up -d --build
```

First run: ~3-5 min (pulls Postgres image, multi-stage build with npm
+ Vite, then bootstrap loads ~1600 shops + WA groups + Fireflies
meetings into Postgres).

Tail the app log:

```bash
docker compose logs -f app
```

What you want to see, in order:

```
[bootstrap] shops in DB: 0
[bootstrap] [1/4] loading shops + contacts + whatsapp_groups
[bootstrap] [2/4] enriching brand names ...
[bootstrap] [3/4] loading finance contacts
[bootstrap] [4/4] loading fireflies meetings
[bootstrap] [5/5] seeding identity graph bindings
[bootstrap] bootstrap complete in 60.0s
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000
```

`Ctrl-C` to detach (containers keep running).

Confirm the app is up internally:

```bash
curl http://127.0.0.1:8000/api/health
# → {"status":"ok", "shops": 1600+, "meetings": ..., ...}
```

If `shops` is 0, bootstrap silently failed — `docker compose logs app
| grep bootstrap` to see the traceback. The container will still be up.

The `calendar-sync` sidecar comes up alongside `app`. Until you complete
step 4b it has no connections to sync and runs an instant no-op every
10 min — fine to leave running.

---

## Step 4b — Enable Google Calendar (optional, do later)

Skip this on first deploy if you just want the URL handed back. Calendar
sync is a separate feature; everything else (search, calls, WhatsApp,
issues) works without it.

**One-time GCP setup** (full walkthrough in `docs/calendar_setup.md`):

1. Enable Calendar API on a GCP project.
2. Create an OAuth 2.0 Client ID (Web application).
   - Authorized redirect URI must be exactly:
     `http://ec2-34-239-140-115.compute-1.amazonaws.com/auth/google/callback`
   - When the senior swaps in HTTPS + a real domain, add that redirect
     URI too — Google permits multiple.
3. Configure OAuth consent screen, **User type: Internal**, scope
   `https://www.googleapis.com/auth/calendar.readonly`.

**On the EC2 box** — fill these into `.env` (the Calendar block is
already commented in `.env.example`):

```bash
GOOGLE_CLIENT_ID=<from GCP>
GOOGLE_CLIENT_SECRET=<from GCP>
GOOGLE_REDIRECT_URI=http://ec2-34-239-140-115.compute-1.amazonaws.com/auth/google/callback
GOOGLE_WORKSPACE_DOMAIN=bitespeed.co
CALENDAR_TOKEN_ENCRYPTION_KEY=<run the command below to generate>
```

Generate the Fernet key once (used to encrypt refresh tokens at rest):

```bash
docker compose exec app python -c \
  "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Apply the new env to both `app` and `calendar-sync` containers:

```bash
docker compose up -d
```

(No `--build` — env-only change. The image doesn't need to rebuild.)

**Each AM connects their calendar:**

Open in a browser (basic-auth into the dashboard first):

```
http://ec2-34-239-140-115.compute-1.amazonaws.com/auth/google/connect
```

Google's consent screen → grant → returns to `/app/?google_connected=1`
with a "Calendar connected" toast. The search screen now shows a green
"Calendar: N accounts · synced …" badge.

**Verify the sidecar is syncing:**

```bash
docker compose logs --tail=50 calendar-sync
# expect: "syncing <user>@bitespeed.co (mode=user_oauth)"
#         "→ <user>: fetched=N upserted=N resolved=M"
```

The dashboard's **Upcoming** tab on a merchant profile reads from
`calendar_events` directly — events appear within a sync cycle (max
10 min). If the merchant has no upcoming events whose attendee emails
resolve to their `shop_url` via the identity graph, the tab stays
empty (this is by design — see `docs/db_schema.md` "Identity graph").

---

## Step 5 — nginx as reverse proxy

```bash
sudo nano /etc/nginx/sites-available/cs-crm
```

Paste **exactly** this:

```nginx
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    client_max_body_size 25M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 90s;
    }
}
```

Save. Enable it and disable the stock `default` site (so this one wins
the `default_server` slot):

```bash
sudo ln -sf /etc/nginx/sites-available/cs-crm /etc/nginx/sites-enabled/cs-crm
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
```

`nginx -t` must end with:

```
nginx: configuration file /etc/nginx/nginx.conf test is successful
```

If it doesn't, paste the exact error to the senior — don't reload a
broken config.

```bash
sudo systemctl reload nginx
```

---

## Step 6 — Smoke test from outside

From your laptop:

```bash
curl -i http://ec2-34-239-140-115.compute-1.amazonaws.com/api/health
# expected: HTTP/1.1 200 OK + JSON {"status":"ok", ...}
```

Then in a browser:

```
http://ec2-34-239-140-115.compute-1.amazonaws.com/app/
```

You should see a basic-auth prompt. Username `bitespeed`, password is
whatever you put in `API_PASSWORD`. After auth → the dashboard.

---

## Step 7 — Hand off to Arindam

Paste-ready Slack message:

```
Bhai app deployed on the EC2 box, nginx setup done.
URL: http://ec2-34-239-140-115.compute-1.amazonaws.com/
Health: /api/health
Frontend: /app/  (basic auth: bitespeed / <password>)
DB schema: docs/db_schema.md
Postgres + app dono iss box pe Docker mein chal rahe hain.
Aage ka SSL/DNS aap dekh lo.
```

(Send the password out-of-band — DM, not in the channel.)

---

## Step 8 — Troubleshooting (the four things that actually go wrong)

| Symptom | Root cause | Fix |
|---|---|---|
| `docker: command not found` or `permission denied` on docker socket | `usermod -aG docker` hasn't taken effect in your current shell | Log out, SSH back in. `groups` should now list `docker`. |
| `502 Bad Gateway` from nginx | App container is down or crashing | `docker compose ps` — is it up? `docker compose logs app` — what's the traceback? |
| `connection refused` from outside the box | SG port 80 not open | Check SG in AWS Console; ping Arindam to open it. |
| Browser doesn't prompt for basic auth at `/app/` | `API_USERNAME` / `API_PASSWORD` missing in `.env` (the app returns 503 instead of 401 in this case — see logs) | Edit `.env`, then `docker compose up -d` to restart with the new env. |
| Frontend renders 404s for `/app/` | Frontend dist not built into the image | `docker compose up -d --build` (the `--build` flag is the important bit). |

---

## Day-2 ops cheatsheet

```bash
# stop / start
docker compose down
docker compose up -d

# pull new code + redeploy
git pull
docker compose up -d --build

# logs
docker compose logs -f app
docker compose logs -f postgres

# psql shell
docker compose exec postgres psql -U cs_crm -d cs_crm

# run a backfill script in the app container
docker compose exec app python scripts/backfill_periskope.py

# nuke the DB volume (DESTROYS DATA)
docker compose down -v
docker compose up -d
```

Quick manual Postgres backup:

```bash
docker compose exec -T postgres pg_dump -U cs_crm cs_crm \
  | gzip > ~/cs_crm_$(date +%F).sql.gz
```
