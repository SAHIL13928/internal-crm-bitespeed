# EC2 deploy — one-script operator note

One bash script. Copy to the box, run, done.

Target: `http://ec2-44-193-212-34.compute-1.amazonaws.com/`
Box: Amazon Linux 2023, ec2-user, sudo passwordless.

---

## Upload + run

From your laptop (in this repo):

```bash
scp -i ~/.ssh/cs-crm.pem deploy/full_deploy.sh \
    ec2-user@ec2-44-193-212-34.compute-1.amazonaws.com:~/full_deploy.sh
ssh -i ~/.ssh/cs-crm.pem ec2-user@ec2-44-193-212-34.compute-1.amazonaws.com
bash ~/full_deploy.sh
```

That's it. On a fresh box the first run takes 4–6 minutes (image pull
+ multi-stage frontend build + Postgres bootstrap of ~1600 shops, WA
groups, Fireflies meetings).

When it succeeds you'll get a **DEPLOY SUMMARY** block with the public
URL and the basic-auth password (auto-generated, written to
`/home/ec2-user/cs-crm/.env`). Save the password — losing it locks you
out of `/app/`.

---

## Re-running is safe

Every step is idempotent and fixes forward:

- repo already cloned → pulls latest
- `.env` already there → leaves your secrets alone, only warns if a
  key from `.env.example` is missing
- containers already up → `docker compose up -d --build` rebuilds in
  place, keeps the Postgres volume
- nginx config already correct → reload, not rewrite

Run `bash ~/full_deploy.sh` any time you want to re-pull + redeploy.

---

## First-run gotcha — docker group re-login

On a truly-fresh box where ec2-user isn't in the `docker` group yet,
the script adds them and **exits** with a "DO THIS, THEN RE-RUN ME"
message. That's expected. The fix:

```bash
exit                # log out of SSH
ssh -i ...          # log back in (now you're in the docker group)
bash ~/full_deploy.sh
```

`usermod -aG docker` only takes effect on new logins — there's no way
around it.

---

## What to do if it fails

Read the **last block of output**. The script always exits with a
labelled `[deploy HH:MM:SS] FATAL: ...` message that points at the
actual problem, plus the relevant log tail (app logs on a startup
failure, postgres logs on a DB failure, nginx error on a config
failure). The most common failures and fixes:

| Failure block says                              | Fix                                                                                            |
|-------------------------------------------------|------------------------------------------------------------------------------------------------|
| `added you to docker group … re-login`          | `exit`, SSH back in, re-run                                                                    |
| `app didn't start within 90s`                   | Read the log tail above the FATAL line. Usually `.env` missing a required value.               |
| `health check did not return 200`               | App + DB log tails are dumped above. Usually a Postgres connection issue or a bootstrap crash. |
| `nginx config test failed`                      | Output of `nginx -t` is dumped above. Almost always a stale conflicting site in `/etc/nginx/`. |
| `docker-compose-plugin not in dnf repos`        | Script auto-falls-back to a manual binary install. If even that fails, your box has no internet egress — open the SG / NAT. |

---

## Manual env vars to fill

The script generates secrets it can (webhook secrets, admin secret,
basic-auth password). It can't generate **API keys** for third-party
services. After the first run, the summary block lists which env vars
are still blank. Edit `~/cs-crm/.env`, fill the ones you need, then:

```bash
cd ~/cs-crm
docker compose up -d        # no --build — env-only restart
```

Currently these are operator-supplied:

- `FREJUN_API_KEY`, `PERISKOPE_API_KEY`, `PERISKOPE_PHONE` — needed
  only for manual backfill scripts. Webhook ingestion works without.
- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REDIRECT_URI`
  / `GOOGLE_WORKSPACE_DOMAIN` / `CALENDAR_TOKEN_ENCRYPTION_KEY` —
  needed only for Calendar sync. App boots and serves all other
  features without these; calendar endpoints return 503 until
  configured. See `docs/calendar_setup.md` when you're ready.

---

## Re-run with a fresh DB (rare, destructive)

This **wipes Postgres data** — the bootstrap will re-load from the
committed CSVs/JSONs but you'll lose any webhook-ingested data, all
backfilled WhatsApp messages, and all manually-entered notes/issues.

```bash
cd ~/cs-crm
docker compose down -v          # -v deletes the postgres_data volume
bash ~/full_deploy.sh
```

If you only want to rebuild the app image (e.g. after a code change)
without touching the DB:

```bash
cd ~/cs-crm
git pull
docker compose up -d --build
```

(or just re-run `bash ~/full_deploy.sh` — same effect, plus health checks)

---

## Files this script touches on the box

- `/home/ec2-user/cs-crm/` — clone of the repo
- `/home/ec2-user/cs-crm/.env` — created on first run (chmod 600)
- `/etc/nginx/conf.d/cs-crm.conf` — proxy 80 → 127.0.0.1:8000
- `/etc/nginx/nginx.conf` — `default_server` keyword moved off port 80
  so our config can claim it (one-time edit, idempotent on re-runs)
- docker volume `cs_crm_postgres_data` — Postgres data, persists across
  `docker compose down`, lost on `docker compose down -v`
