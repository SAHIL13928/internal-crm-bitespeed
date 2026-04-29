# Multi-stage build:
#   Stage 1 — Node 20 builds the TypeScript/Vite frontend → /app/frontend/dist
#   Stage 2 — Python 3.11 slim image installs deps, copies app + frontend dist,
#             runs the bootstrap script then uvicorn
#
# Works on AWS App Runner, ECS Fargate, Beanstalk-with-Docker, EC2, or any
# Docker host. Render also accepts this Dockerfile if you ever switch back.

# ── Stage 1: build the frontend ────────────────────────────────────────────
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend

# Copy package files first so this layer caches when only source changes.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

# Now copy the rest and build.
COPY frontend/ ./
RUN npm run build


# ── Stage 2: Python app ────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime
WORKDIR /app

# `libpq5` is the runtime library psycopg2-binary depends on. wget is for
# the HEALTHCHECK below. Slim image is intentional — keep the surface small.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 wget \
 && rm -rf /var/lib/apt/lists/*

# Install Python deps before copying app code so this layer caches.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code + ETL inputs (CSVs/JSONs ship in the image so the
# bootstrap script can populate Postgres on first boot).
COPY crm_app/ ./crm_app/
COPY etl/ ./etl/
COPY scripts/ ./scripts/
COPY data/ ./data/
COPY migrations/ ./migrations/
COPY run_etl.py ./

# Pull frontend build artifact from stage 1 — FastAPI mounts this at /app/.
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

# AWS App Runner expects port 8000 by default. Override with $PORT for ECS,
# Beanstalk, etc. that inject their own port.
ENV PORT=8000
EXPOSE 8000

# Liveness probe — uses the unauthenticated /api/health endpoint so AWS
# can poll it without credentials.
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://localhost:${PORT}/api/health || exit 1

# Bootstrap is idempotent — fast no-op (~1s) when shops table is already
# populated, full ETL (~60s) only on a fresh database.
CMD ["sh", "-c", "python scripts/bootstrap_render.py && exec uvicorn crm_app.main:app --host 0.0.0.0 --port ${PORT}"]
