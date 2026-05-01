"""Idempotent boot-time bootstrap for Render's free tier.

Render free tier has ephemeral disk — every cold start (after sleep or
redeploy) gets a fresh filesystem, which means `crm.db` is gone. We
ship the static-directory input files in git and rebuild the DB from
them at startup if it's missing.

Designed to be **fast on the no-op path** (warm restart, DB already
populated → returns in <1s) and **complete in ~60s on cold start**.

Skipped: FreJun historical backfill. Calls populate via the live
webhook going forward; backfilling 44k records on every cold start
would blow past Render's bind timeout.

Usage (in render.yaml startCommand):
    python scripts/bootstrap_render.py && uvicorn crm_app.main:app ...
"""
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [bootstrap] %(message)s")
log = logging.getLogger("bootstrap_render")


def needs_bootstrap() -> bool:
    """True iff the DB is missing or has zero shops."""
    from crm_app.db import Base, SessionLocal, engine
    from crm_app.models import Shop

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        n = db.query(Shop).count()
        log.info("shops in DB: %d", n)
        return n == 0
    finally:
        db.close()


def run_bootstrap():
    """Populate the DB from committed input files. Each step is itself
    idempotent so partial runs (interrupted boot) recover on retry."""
    t0 = time.time()
    log.info("starting bootstrap ...")

    from etl.load_shops import load_shops
    log.info("[1/4] loading shops + contacts + whatsapp_groups")
    load_shops()

    from etl.enrich_shops import load_brand_names
    log.info("[2/4] enriching brand names (best-effort, skips if XLSX missing)")
    load_brand_names()

    from etl.load_finance_contacts import load_finance_contacts
    log.info("[3/4] loading finance contacts")
    load_finance_contacts()

    from etl.load_fireflies import load_fireflies
    log.info("[4/4] loading fireflies meetings")
    load_fireflies()

    # Seed the identity graph so the first webhook event can resolve via
    # the graph (otherwise it'd start with an empty graph and only
    # resolve via the static phone_to_shop dict).
    from scripts.recompute_static_directory_bindings import main as recompute_main
    log.info("[5/5] seeding identity graph bindings")
    # The recompute script reads argv; pass an empty arg list.
    sys.argv = ["recompute_static_directory_bindings.py"]
    recompute_main()

    log.info("bootstrap complete in %.1fs", time.time() - t0)


def main():
    # Guarded so a bootstrap failure (bad CSV, transient DB connect
    # issue) doesn't crash the container. With docker-compose's
    # `restart: unless-stopped`, a hard exit triggers a tight restart
    # loop and we lose the very logs we'd need to debug it. Log the
    # full traceback and exit 0 — the API still comes up, /api/health
    # will report shops:0 so the operator knows ETL didn't run.
    try:
        if not needs_bootstrap():
            log.info("DB already populated, skipping ETL")
            return
        run_bootstrap()
    except Exception:
        log.exception("bootstrap failed — continuing so the API still starts")


if __name__ == "__main__":
    main()
