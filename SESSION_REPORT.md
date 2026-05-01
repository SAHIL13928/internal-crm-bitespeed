# Session report — EC2 single-box deploy prep

**Date:** 2026-05-01
**Goal:** stand up Postgres + the FastAPI app + nginx on the
EC2 box `ec2-34-239-140-115.compute-1.amazonaws.com`. Senior takes over
DNS / SSL after the URL is handed back.

---

## What changed (file-by-file)

All edits except one are to existing files, per directive.

### Edited

- **`docker-compose.yml`** — three things:
  - App container's port published to `127.0.0.1:8000:8000` (was
    `8000:8000`). nginx on the host is now the only public path; even
    if the SG opens 8000 it's not reachable.
  - Pass-through for the Google Calendar env vars
    (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`,
    `GOOGLE_SERVICE_ACCOUNT_JSON`, `GOOGLE_WORKSPACE_DOMAIN`,
    `CALENDAR_TOKEN_ENCRYPTION_KEY`) added so the calendar feature
    works in prod when configured.
  - New env `CRM_REQUIRE_DATABASE_URL=1` set on the app container —
    sentinel that flips db.py from "fall back to SQLite" to "raise
    RuntimeError" when DATABASE_URL is missing inside the container.

- **`crm_app/db.py`** — fail fast on missing `DATABASE_URL` when the
  prod sentinel `CRM_REQUIRE_DATABASE_URL` is set. Local dev still
  silently falls back to SQLite (sentinel is unset there).

- **`crm_app/main.py`** — log a warning at startup when
  `ALLOWED_ORIGINS` is unset (was a silent `*` fallback). Easier to
  catch the misconfig in `docker compose logs`.

- **`scripts/bootstrap_render.py`** — wrap `main()` in try/except. A
  bootstrap failure now logs full traceback and exits 0 so the API
  still comes up. Without this, `restart: unless-stopped` produced a
  tight loop that obscured the actual error.

- **`.env.example`** — added the Google Calendar / Fernet env vars
  (with comments noting they're optional), updated the Postgres header
  comment to clarify that `DATABASE_URL` is auto-built by compose, set
  the example `ALLOWED_ORIGINS` to the real EC2 host.

- **`docs/ec2.md`** — replaced wholesale with a single 8-step
  walkthrough: local PEM prep + SSH → docker install + the
  re-login gotcha → clone → `.env` → `docker compose up` → nginx
  reverse proxy with the exact server block → external smoke tests →
  Slack handoff to Arindam → troubleshooting (the four things that
  actually break). No second walkthrough appended; old certbot/443
  section removed because senior owns SSL.

### New (only file created this session, per allowance)

- **`docs/db_schema.md`** — table-by-table reference: shops, contacts,
  comms (meetings/calls/whatsapp), issues/notes, identity graph
  (nodes, undirected bindings, BFS-depth-3 resolution semantics with
  `conflict` sentinel), calendar tables, bootstrap data flow from
  `data/inputs/*` → tables, migration model (PRAGMA on SQLite, manual
  SQL files on Postgres). Tone matches the request: terse, technical.

### Untouched (and why)

- `Dockerfile` — already binds `0.0.0.0` inside the container, has a
  healthcheck on `/api/health`, multi-stage frontend build.
- `render.yaml` / `apprunner.yaml` — out of scope; we're on EC2.
- `docs/aws.md`, `docs/deploy.md` — orthogonal walkthroughs; left
  alone to avoid scope creep.
- `crm_app/main.py::/api/health` — already exists, no auth, returns
  `{"status": "ok"}` plus diagnostic counts. No change needed.

---

## Tests

```
$ venv/Scripts/python.exe -m pytest tests/ \
    --ignore=tests/smoke_test_50.py \
    --ignore=tests/smoke_test_frejun_webhook.py \
    --ignore=tests/smoke_test_whatsapp_webhook.py

============================= test session starts =============================
platform win32 -- Python 3.12.3, pytest-9.0.3, pluggy-1.6.0
collected 43 items

tests/test_calendar_oauth.py ...                                         [  6%]
tests/test_calendar_sync.py ......                                       [ 20%]
tests/test_frejun_webhook.py ....                                        [ 30%]
tests/test_identity_graph.py ........                                    [ 48%]
tests/test_periskope_webhook.py .............                            [ 79%]
tests/test_whatsapp_ingestion.py .........                               [100%]

====================== 43 passed, 10 warnings in 50.95s =======================
```

Only warnings are `datetime.utcnow()` deprecations in calendar code
and the `authlib.jose` deprecation — pre-existing, not introduced
this session.

`smoke_test_*.py` files are ignored because they hit live external
webhooks (FreJun / Periskope) and require network + provider creds.

---

## Fresh secrets generated this session (paste once into `.env` on EC2)

These were generated with `python -c "import secrets;
print(secrets.token_urlsafe(...))"`. Use them for the
**first-deploy** values; rotate later if leaked.

```
POSTGRES_PASSWORD=PBxJh3ZbAsOxZYv5uoMZrCLB6HFHkbxq
API_PASSWORD=pH81FmwP3EGA-qjRJbBBj3GlTZ9k4Uj8
ADMIN_SECRET=hARbf0W9hs6Dotoa21j_XeMw_lVZ5mkfUHhXsFx4Wyk
```

Don't paste these into Slack / GitHub. `.env` on the EC2 box is
`chmod 600` (the runbook tells you to do it).

---

## Operator runbook

**`docs/ec2.md`** — single source of truth for the deploy. Every
command is copy-pasteable. The four sharp edges called out:

1. `usermod -aG docker` requires re-login.
2. App container is bound to `127.0.0.1` — port 8000 in the SG is a
   no-op.
3. `nginx -t` must say "test is successful" before reloading.
4. Bootstrap silently logging-and-continuing means a `shops:0` health
   response is still possible — `docker compose logs app | grep
   bootstrap` is the diagnostic.

---

## Slack message for Arindam (paste-ready, post-deploy)

```
Bhai app deployed on the EC2 box, nginx setup done.
URL: http://ec2-34-239-140-115.compute-1.amazonaws.com/
Health: /api/health
Frontend: /app/  (basic auth: bitespeed / <password>)
DB schema: docs/db_schema.md
Postgres + app dono iss box pe Docker mein chal rahe hain.
Aage ka SSL/DNS aap dekh lo.
```

Send the basic-auth password via DM, not in the channel.

---

## Blocked / out of scope

- **No SSH access from the AI side** — every command is run by the
  operator from `docs/ec2.md`. This report doesn't include "I ran
  this on the box" because we haven't.
- **No git push** — operator commits + pushes when ready.
- **DNS / SSL / certbot** — senior owns this after URL handoff.
- **SG rule for port 80** — operator confirms or pings Arindam with
  the exact one-liner in step 1.
