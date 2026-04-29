# Deploy to a single EC2 box

> Single Linux machine running Postgres + the FastAPI app side-by-side
> via Docker Compose. Cheaper than App Runner + RDS, more manual ops.
> Good for an internal demo / staging. ~30 min to first URL.

## What you're standing up

```
your laptop  ──ssh──►  EC2 instance (Ubuntu)
                          │
                          ├─ docker compose up
                          │     ├── postgres container  (data on a volume)
                          │     └── app container       (FastAPI, port 8000)
                          │
                          └─ optional: nginx (port 80/443) → app:8000
                                       certbot (free Let's Encrypt cert)
```

## 0. Before you SSH

On your laptop:

```bash
# Your private key should be saved somewhere local. Lock it down so SSH
# doesn't refuse it.
chmod 600 ~/.ssh/cs-crm-ec2.pem
```

Open the EC2 security group (AWS Console → EC2 → your instance →
Security tab → click the SG → Edit inbound rules) and allow:

| Port | Source | Why |
|---|---|---|
| 22  | your IP | SSH |
| 80  | 0.0.0.0/0 | HTTP (for nginx + Let's Encrypt) |
| 443 | 0.0.0.0/0 | HTTPS |
| 8000 | your IP **only**, temporarily | direct app access while debugging |

## 1. SSH in

```bash
ssh -i ~/.ssh/cs-crm-ec2.pem ubuntu@<EC2-PUBLIC-IP>
# (use ec2-user@... if it's an Amazon Linux AMI, root@... rare)
```

Everything below runs **on the EC2 box**.

## 2. Install Docker + Compose plugin

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2 git
sudo usermod -aG docker $USER
# Log out + back in so the group change takes effect, or run:
newgrp docker
docker --version && docker compose version
```

## 3. Clone the repo

```bash
cd ~
git clone https://github.com/<your-org>/<your-repo>.git cs-crm
cd cs-crm
```

(If the repo is private, configure a GitHub deploy key on the EC2 or
clone via HTTPS with a personal access token.)

## 4. Set up the secrets file

```bash
cp .env.example .env
nano .env
```

Fill in **every value**. Critical ones:

- `POSTGRES_PASSWORD` — pick a long random string. Generate one:
  ```bash
  python3 -c "import secrets; print(secrets.token_urlsafe(32))"
  ```
- `API_PASSWORD` — same generator.
- `ALLOWED_ORIGINS` — start with `http://<EC2-PUBLIC-IP>:8000` for
  initial testing. Update to your `https://your-domain.com` after
  nginx is set up.
- All four webhook secrets — these need to match what's configured on
  FreJun / Periskope console / the WA bridge.
- `ADMIN_SECRET` — long random.

Save (`Ctrl-O`, `Enter`, `Ctrl-X`).

## 5. Build + run the stack

```bash
docker compose up -d --build
```

First time: ~3-5 min (Docker pulls Postgres image, builds the app
multi-stage Dockerfile, npm installs, Vite builds the frontend).

Watch it come up:

```bash
docker compose logs -f app
```

You'll see the bootstrap script populate the database from CSVs:

```
[bootstrap] shops in DB: 0
[bootstrap] [1/5] loading shops + contacts + whatsapp_groups
[bootstrap] [2/5] enriching brand names ...
[bootstrap] [3/5] loading finance contacts
[bootstrap] [4/5] loading fireflies meetings
[bootstrap] [5/5] seeding identity graph bindings
[bootstrap] bootstrap complete in 60.0s
INFO: Uvicorn running on http://0.0.0.0:8000
```

Press `Ctrl-C` to detach (containers keep running).

## 6. Smoke test on the EC2

```bash
curl http://localhost:8000/api/health
# → JSON with shops > 1600, calls > 0, etc.

curl -u 'bitespeed:<API_PASSWORD>' \
     http://localhost:8000/api/merchants?q=avishya
# → JSON array with the merchant
```

If `shops` is `0`, the bootstrap didn't run. Check `docker compose logs app`.

## 7. Verify from your laptop (HTTP, temporary)

```bash
curl http://<EC2-PUBLIC-IP>:8000/api/health
```

If that works, you have a live URL. Send your manager
`http://<EC2-PUBLIC-IP>:8000/app/` and they can browse.

> **Don't ship over plain HTTP for long.** Browsers warn on basic-auth
> over HTTP. Set up HTTPS in step 8.

## 8. (recommended) HTTPS via nginx + Let's Encrypt

You need a domain pointing at the EC2 IP — point an A record at the
EC2 public IP. Wait for DNS to propagate (~5 min).

```bash
sudo apt-get install -y nginx certbot python3-certbot-nginx
```

Create the nginx config:

```bash
sudo nano /etc/nginx/sites-available/cs-crm
```

Paste:

```nginx
server {
    listen 80;
    server_name your-domain.com;

    client_max_body_size 5M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 90s;
    }
}
```

Save, enable, reload:

```bash
sudo ln -s /etc/nginx/sites-available/cs-crm /etc/nginx/sites-enabled/
sudo rm /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

Now grab a free Let's Encrypt cert:

```bash
sudo certbot --nginx -d your-domain.com
# Follow the prompts. Choose "redirect HTTP → HTTPS" when asked.
```

certbot will auto-renew via a cron job — nothing else to do.

After this works, **close port 8000** in the security group (only nginx
on 80/443 should be public-facing).

Update `ALLOWED_ORIGINS` in `.env` to `https://your-domain.com` and
restart the app:

```bash
docker compose up -d
```

## Day-2 ops

```bash
# Stop / start
docker compose down
docker compose up -d

# Pull new code + redeploy
git pull
docker compose up -d --build

# View logs
docker compose logs -f app
docker compose logs -f postgres

# psql into Postgres
docker compose exec postgres psql -U cs_crm -d cs_crm

# Run a backfill script inside the app container
docker compose exec app python scripts/backfill_periskope.py

# Reset the database (DESTROYS DATA)
docker compose down -v
docker compose up -d
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ssh: connection refused` | Security group doesn't allow port 22 from your IP |
| `permission denied (publickey)` | Wrong username (`ubuntu` vs `ec2-user`), or key not 600 |
| `docker compose up` build fails on `npm ci` | Make sure `frontend/package-lock.json` is committed in the repo |
| App keeps crashing — `connection refused` | Postgres not ready yet. `depends_on: condition: service_healthy` should handle it; if not, restart with `docker compose up -d` |
| `/api/health` shows `shops: 0` after boot | Bootstrap script failed silently. `docker compose logs app | grep bootstrap` |
| Browser warns "Not secure" | You're on HTTP. Finish step 8. |
| `curl` hangs from laptop | Security group blocking 80/443/8000 |

## Backups

Postgres data lives on a Docker volume named `cs_crm_postgres_data` on
the EC2 host's local disk. **No automated backups by default.** For a
demo it's fine; for production:

```bash
# Quick manual backup
docker compose exec -T postgres pg_dump -U cs_crm cs_crm | gzip > /tmp/cs_crm_$(date +%F).sql.gz

# Restore
gunzip -c /tmp/cs_crm_2026-04-30.sql.gz | docker compose exec -T postgres psql -U cs_crm cs_crm
```

For real production, set up a daily cron that uploads to S3.

## Security checklist before sharing the URL

- [ ] HTTPS working (step 8)
- [ ] Port 8000 closed in security group (only 80/443 public)
- [ ] `API_PASSWORD` is strong and not shared in chat/screenshots
- [ ] `.env` file on the EC2 is `chmod 600`: `chmod 600 ~/cs-crm/.env`
- [ ] SSH key removed from anywhere it leaked (chat, Slack)
- [ ] `ALLOWED_ORIGINS` is your https domain only, not `*`
