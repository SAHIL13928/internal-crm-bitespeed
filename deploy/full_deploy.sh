#!/usr/bin/env bash
# CS-CRM single-box EC2 deploy script.
#
# Run this on a fresh (or partially-set-up) Amazon Linux 2023 EC2 box as
# the ec2-user. It is safe to re-run: every step is idempotent and fixes
# forward.
#
#   bash ~/full_deploy.sh
#
# When it succeeds, the CRM is live at http://<this-box>/.
# When it fails, the last block of output tells you exactly what's wrong.

set -euo pipefail

# ─────────────────────────── config ────────────────────────────────────
REPO_URL="https://github.com/SAHIL13928/internal-crm-bitespeed.git"
APP_DIR="/home/ec2-user/cs-crm"
APP_PORT=8000          # internal — bound to 127.0.0.1 by docker-compose
PUBLIC_PORT=80         # nginx fronts the world
NGINX_CONF=/etc/nginx/conf.d/cs-crm.conf
HEALTH_PATH=/api/health
APP_READY_TIMEOUT=90   # seconds to wait for "Application startup complete"

# ─────────────────────────── helpers ───────────────────────────────────
log()   { echo "[deploy $(date +%H:%M:%S)] $*"; }
warn()  { echo "[deploy $(date +%H:%M:%S)] WARN: $*"; }
fatal() {
    echo
    echo "[deploy $(date +%H:%M:%S)] FATAL: $*" >&2
    echo "[deploy $(date +%H:%M:%S)] aborting. fix the above, then re-run: bash ~/full_deploy.sh" >&2
    echo
    exit 1
}

# ─────────────────────── step 1: sanity ────────────────────────────────
log "starting full_deploy.sh"

[[ "$(uname -s)" == "Linux" ]] || fatal "this script is Linux-only (uname=$(uname -s))"

if [[ "$EUID" -eq 0 ]]; then
    fatal "do not run as root. run as ec2-user (passwordless sudo is used internally)."
fi

if [[ "$(whoami)" != "ec2-user" ]]; then
    warn "expected user ec2-user, got $(whoami) — paths assume /home/ec2-user/. continuing."
fi

# We need: git, docker, docker compose plugin, nginx, python3, curl.
need_install=()
command -v git    >/dev/null 2>&1 || need_install+=(git)
command -v docker >/dev/null 2>&1 || need_install+=(docker)
command -v nginx  >/dev/null 2>&1 || need_install+=(nginx)
command -v curl   >/dev/null 2>&1 || need_install+=(curl)
command -v python3 >/dev/null 2>&1 || need_install+=(python3)

# `docker compose` (plugin) — separate check, lives outside $PATH lookup.
have_compose_plugin=0
if docker compose version >/dev/null 2>&1; then
    have_compose_plugin=1
fi

if (( ${#need_install[@]} > 0 )); then
    log "installing missing packages via dnf: ${need_install[*]}"
    sudo dnf install -y "${need_install[@]}"
fi

if (( have_compose_plugin == 0 )); then
    log "installing docker compose plugin"
    if ! sudo dnf install -y docker-compose-plugin 2>/dev/null; then
        warn "docker-compose-plugin not in dnf repos; installing v2 binary manually"
        sudo mkdir -p /usr/libexec/docker/cli-plugins
        sudo curl -sSL \
            "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64" \
            -o /usr/libexec/docker/cli-plugins/docker-compose
        sudo chmod +x /usr/libexec/docker/cli-plugins/docker-compose
    fi
    docker compose version >/dev/null 2>&1 \
        || fatal "docker compose plugin still not working after install attempt"
fi

# Docker daemon up & enabled.
if ! systemctl is-active --quiet docker; then
    log "enabling + starting docker daemon"
    sudo systemctl enable --now docker
fi

# Docker group membership. usermod doesn't apply to current shell, so if
# we're not yet a member we add and exit with re-login instructions.
if ! id -nG "$(whoami)" | tr ' ' '\n' | grep -qx docker; then
    log "adding $(whoami) to docker group"
    sudo usermod -aG docker "$(whoami)"
    cat <<MSG

[deploy $(date +%H:%M:%S)] DO THIS, THEN RE-RUN ME:

  1. exit            # log out of this SSH session
  2. ssh back in     # so the new docker group membership applies
  3. bash ~/full_deploy.sh

(Reason: usermod only updates membership for *new* logins. Without this
re-login, every \`docker\` command in this session would fail with
"permission denied on /var/run/docker.sock".)

MSG
    exit 0
fi

# Final docker reachability check — catches the case where group is set
# but daemon isn't accepting connections yet.
if ! docker ps >/dev/null 2>&1; then
    fatal "\`docker ps\` failed even though you're in the docker group. is the daemon running? try: sudo systemctl status docker"
fi

log "sanity OK: linux, docker, compose plugin, nginx, git, python3 all present"

# ─────────────────────── step 2: code ──────────────────────────────────
if [[ ! -d "${APP_DIR}/.git" ]]; then
    log "cloning ${REPO_URL} → ${APP_DIR}"
    git clone "${REPO_URL}" "${APP_DIR}"
else
    log "repo already at ${APP_DIR}, pulling latest"
    git -C "${APP_DIR}" fetch --all --prune
    git -C "${APP_DIR}" pull --rebase --autostash
fi

cd "${APP_DIR}"

# ─────────────────────── step 3: .env ──────────────────────────────────
# Helper: write or update a single KEY=value line in .env, idempotent and
# safe for values containing /, &, |, etc. (uses python, not sed).
upsert_env() {
    python3 - "$1" "$2" <<'PY'
import sys, os
key, val = sys.argv[1], sys.argv[2]
path = ".env"
lines = []
if os.path.exists(path):
    with open(path) as f:
        lines = f.read().splitlines()
seen = False
for i, ln in enumerate(lines):
    s = ln.lstrip()
    if not s or s.startswith("#"):
        continue
    if "=" not in s:
        continue
    k = s.split("=", 1)[0].strip()
    if k == key:
        lines[i] = f"{key}={val}"
        seen = True
        break
if not seen:
    lines.append(f"{key}={val}")
with open(path, "w") as f:
    f.write("\n".join(lines) + "\n")
PY
}

get_env_val() {
    # prints empty string if missing
    python3 - "$1" <<'PY'
import sys, os
key = sys.argv[1]
path = ".env"
if not os.path.exists(path):
    print("")
    sys.exit(0)
with open(path) as f:
    for ln in f:
        s = ln.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        if k.strip() == key:
            print(v)
            sys.exit(0)
print("")
PY
}

gen_secret() {
    python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
}

# Values we know are placeholders (from .env.example) and want to rotate.
PLACEHOLDER="replace-with-long-random-string"

is_placeholder() {
    local v="$1"
    [[ -z "$v" || "$v" == "$PLACEHOLDER" ]]
}

NEW_ENV=0
if [[ -f .env ]]; then
    log ".env exists — leaving values alone (idempotent)"
    # Warn (don't fail) on keys defined in .env.example but missing in .env.
    if [[ -f .env.example ]]; then
        missing_keys=()
        while IFS= read -r line; do
            s="${line## }"
            [[ -z "$s" || "$s" == \#* ]] && continue
            [[ "$s" != *=* ]] && continue
            k="${s%%=*}"
            k="${k// /}"
            [[ -z "$k" ]] && continue
            if ! grep -qE "^[[:space:]]*${k}=" .env; then
                missing_keys+=("$k")
            fi
        done < .env.example
        if (( ${#missing_keys[@]} > 0 )); then
            warn "keys in .env.example but missing from .env (features tied to them won't work):"
            for k in "${missing_keys[@]}"; do
                warn "  - $k"
            done
        fi
    fi
else
    log ".env missing — copying from .env.example and generating fresh secrets"
    cp .env.example .env
    chmod 600 .env
    NEW_ENV=1

    # Postgres: compose builds DATABASE_URL from these three; the password
    # is non-secret because the postgres container has no published port
    # — only the app container on the internal docker network can reach it.
    upsert_env POSTGRES_DB cs_crm
    upsert_env POSTGRES_USER cs_crm
    upsert_env POSTGRES_PASSWORD cs_crm_pass
    # Also stash an explicit DATABASE_URL for any tooling that reads .env
    # directly (compose itself overrides this in the app container env).
    upsert_env DATABASE_URL "postgresql://cs_crm:cs_crm_pass@postgres:5432/cs_crm"

    # Basic-auth on the dashboard / read API.
    upsert_env API_USERNAME bitespeed
    GEN_API_PASSWORD="$(gen_secret)"
    upsert_env API_PASSWORD "$GEN_API_PASSWORD"

    # Wide-open until the senior swaps in a real domain.
    upsert_env ALLOWED_ORIGINS '*'

    # Generate everything we can. Values left as placeholder/empty (and
    # listed at the end of the script) require manual entry.
    upsert_env FREJUN_WEBHOOK_SECRET    "$(gen_secret)"
    upsert_env WHATSAPP_WEBHOOK_SECRET  "$(gen_secret)"
    upsert_env PERISKOPE_SIGNING_SECRET "$(gen_secret)"
    upsert_env ADMIN_SECRET             "$(gen_secret)"

    # Things the script can't generate (real API keys / phone numbers /
    # GCP creds) get explicitly blanked out — they were either empty in
    # the example or carried over a stale value. List them in summary.
    for k in FREJUN_API_KEY PERISKOPE_API_KEY PERISKOPE_PHONE \
             GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET GOOGLE_REDIRECT_URI \
             GOOGLE_SERVICE_ACCOUNT_JSON GOOGLE_WORKSPACE_DOMAIN \
             CALENDAR_TOKEN_ENCRYPTION_KEY; do
        cur="$(get_env_val "$k")"
        if is_placeholder "$cur"; then
            upsert_env "$k" ""
        fi
    done
fi

# ─────────────────────── step 4: stack up ──────────────────────────────
log "docker compose up -d --build (first run pulls images + multi-stage build, ~3-5 min)"
docker compose up -d --build

log "waiting up to ${APP_READY_TIMEOUT}s for app readiness marker"
deadline=$(( $(date +%s) + APP_READY_TIMEOUT ))
ready=0
while (( $(date +%s) < deadline )); do
    if docker compose logs app --tail 400 2>/dev/null \
            | grep -qE "Application startup complete|Uvicorn running on"; then
        ready=1
        break
    fi
    sleep 3
done

if (( ready == 0 )); then
    echo
    log "app failed to come up within ${APP_READY_TIMEOUT}s. last 50 lines of \`docker compose logs app\`:"
    echo "─────────────────────────────────────────────────────────"
    docker compose logs app --tail 50 || true
    echo "─────────────────────────────────────────────────────────"
    fatal "app didn't start. read the log above for the traceback."
fi
log "app started"

# ─────────────────────── step 5: nginx ─────────────────────────────────
# AL2023's stock /etc/nginx/nginx.conf has its own `listen 80 default_server`
# block. Two `default_server`s on the same port = nginx config error, so
# we move the stock site off port 80 (to a closed-by-SG 8081) and put our
# proxy on 80 default_server. Idempotent: if already moved, sed is a no-op.
if grep -qE '^[[:space:]]*listen[[:space:]]+80 default_server;' /etc/nginx/nginx.conf 2>/dev/null; then
    log "moving stock nginx default site off port 80"
    sudo sed -i -E \
        -e 's|^([[:space:]]*)listen[[:space:]]+80 default_server;|\1listen 8081;|' \
        -e 's|^([[:space:]]*)listen[[:space:]]+\[::\]:80 default_server;|\1listen [::]:8081;|' \
        /etc/nginx/nginx.conf
fi

log "writing nginx site config → ${NGINX_CONF}"
sudo tee "${NGINX_CONF}" >/dev/null <<NGINX
# Managed by deploy/full_deploy.sh — re-running the script overwrites this file.
server {
    listen ${PUBLIC_PORT} default_server;
    listen [::]:${PUBLIC_PORT} default_server;
    server_name _;

    client_max_body_size 25M;

    location / {
        proxy_pass http://127.0.0.1:${APP_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 90s;
    }
}
NGINX

if ! sudo nginx -t 2>/tmp/nginx.test; then
    cat /tmp/nginx.test >&2
    fatal "nginx config test failed (see above). don't reload — fix the config first."
fi

if ! systemctl is-enabled --quiet nginx; then
    sudo systemctl enable nginx
fi
if systemctl is-active --quiet nginx; then
    log "reloading nginx"
    sudo systemctl reload nginx
else
    log "starting nginx"
    sudo systemctl start nginx
fi

# ─────────────────────── step 6: health ────────────────────────────────
log "smoke test: curl http://127.0.0.1:${PUBLIC_PORT}${HEALTH_PATH}"
http_code="$(curl -s -o /tmp/cs-crm-health.json -w '%{http_code}' \
    "http://127.0.0.1:${PUBLIC_PORT}${HEALTH_PATH}" || echo 000)"

if [[ "$http_code" != "200" ]]; then
    log "health endpoint returned HTTP ${http_code}"
    log "body: $(cat /tmp/cs-crm-health.json 2>/dev/null || echo '<empty>')"
    echo "──────── docker compose logs app (last 80) ─────────"
    docker compose logs app --tail 80 || true
    echo "──────── docker compose logs postgres (last 30) ─────"
    docker compose logs postgres --tail 30 || true
    echo "─────────────────────────────────────────────────────"
    fatal "health check did not return 200."
fi

if ! grep -q '"status":[[:space:]]*"ok"' /tmp/cs-crm-health.json; then
    log "body: $(cat /tmp/cs-crm-health.json)"
    fatal "health returned 200 but status != ok"
fi

log "health OK. counts:"
python3 - <<'PY'
import json
with open('/tmp/cs-crm-health.json') as f:
    d = json.load(f)
def show(label, v):
    print(f"  {label:>30}: {v}")
for k in ("shops","meetings","calls","calls_with_shop","issues","notes"):
    if k in d: show(k, d[k])
wa = d.get("whatsapp", {}) or {}
for k in ("raw_messages","raw_messages_resolved","groups_known","group_events"):
    if k in wa: show(f"whatsapp.{k}", wa[k])
ig = d.get("identity_graph", {}) or {}
for k in ("identities","bindings"):
    if k in ig: show(f"identity_graph.{k}", ig[k])
PY

# ─────────────────────── step 7: resource snapshot ─────────────────────
log "memory:"
free -h
log "disk (/):"
df -h /

mem_pct="$(free | awk 'NR==2 { if ($2>0) printf("%d", $3*100/$2); else print 0 }')"
HEAVY_WARN=""
if (( mem_pct > 70 )); then
    HEAVY_WARN="memory is at ${mem_pct}% after stack startup. Run backfill in SMALLER chunks (--chunk-size 100-200) to avoid OOM kills, and watch htop in another SSH window."
    warn "$HEAVY_WARN"
fi

# ─────────────────────── step 8: summary ───────────────────────────────
PUB_HOST="$(curl -s --max-time 2 http://169.254.169.254/latest/meta-data/public-hostname 2>/dev/null || true)"
[[ -z "$PUB_HOST" ]] && PUB_HOST="$(hostname -f 2>/dev/null || echo '<this-box>')"

CUR_API_USERNAME="$(get_env_val API_USERNAME)"
CUR_API_PASSWORD="$(get_env_val API_PASSWORD)"

# Anything still empty or still set to the canonical placeholder needs
# manual input from the operator.
unfilled=()
for k in FREJUN_API_KEY PERISKOPE_API_KEY PERISKOPE_PHONE \
         GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET GOOGLE_REDIRECT_URI \
         GOOGLE_SERVICE_ACCOUNT_JSON GOOGLE_WORKSPACE_DOMAIN \
         CALENDAR_TOKEN_ENCRYPTION_KEY; do
    v="$(get_env_val "$k")"
    if is_placeholder "$v"; then
        unfilled+=("$k")
    fi
done

cat <<SUMMARY

═════════════════════════════ DEPLOY SUMMARY ═════════════════════════════
Status:        UP

Public URL:    http://${PUB_HOST}/
Frontend:      http://${PUB_HOST}/app/
Health:        http://${PUB_HOST}/api/health

Basic auth:
  username:    ${CUR_API_USERNAME}
  password:    ${CUR_API_PASSWORD}
  (share password out-of-band — DM, not a shared channel)
SUMMARY

if (( ${#unfilled[@]} > 0 )); then
    echo
    echo "Env vars still set to placeholder/blank — features tied to them"
    echo "won't work until you fill them in ${APP_DIR}/.env and run"
    echo "\`docker compose up -d\` from ${APP_DIR}:"
    for k in "${unfilled[@]}"; do
        echo "  - $k"
    done
fi

cat <<SUMMARY

FreJun backfill (run later, in chunks; keep htop in another SSH window):

  cd ${APP_DIR}
  docker compose exec app python scripts/backfill_frejun.py \\
      --since 2026-01-01 --chunk-size 200

REMINDER: don't run more than one heavy backfill / sync script at a time.
Re-run this script any time:    bash ~/full_deploy.sh   (idempotent)
Tail app logs:                  cd ${APP_DIR} && docker compose logs -f app
Stop everything:                cd ${APP_DIR} && docker compose down
SUMMARY

if [[ -n "$HEAVY_WARN" ]]; then
    echo
    echo "!!! ${HEAVY_WARN}"
fi

if (( NEW_ENV == 1 )); then
    echo
    echo "Generated .env at ${APP_DIR}/.env (chmod 600). Back it up — losing"
    echo "API_PASSWORD locks you out of the dashboard until you set a new one."
fi

echo "═════════════════════════════════════════════════════════════════════════"
log "done."
