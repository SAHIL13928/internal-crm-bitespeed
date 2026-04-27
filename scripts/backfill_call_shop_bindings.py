"""Walk every call with shop_url=NULL and try to re-bind it via the
current static directory + identity graph. Cheap, idempotent.

Use this after:
  • A new contacts CSV is loaded (e.g. finance contacts)
  • Bindings are recomputed (`recompute_static_directory_bindings.py`)
  • A manual binding is added by an operator

Usage:
    python scripts/backfill_call_shop_bindings.py [--dry-run] [--limit N]
"""
import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm_app.db import SessionLocal  # noqa: E402
from crm_app.models import Call  # noqa: E402
from crm_app.resolver import resolve_call  # noqa: E402
from crm_app.utils import build_phone_to_shop  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backfill_call_shop_bindings")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()

    db = SessionLocal()
    try:
        q = (
            db.query(Call)
            .filter(Call.shop_url.is_(None))
            .order_by(Call.started_at.desc().nullslast())
        )
        if args.limit:
            q = q.limit(args.limit)
        rows = q.all()
        log.info("orphan calls to revisit: %d", len(rows))

        # Build the phone_to_shop dict once and reuse — avoids quadratic
        # cost when scanning tens of thousands of calls.
        phone_to_shop = build_phone_to_shop(db)
        log.info("phone_to_shop entries:   %d", len(phone_to_shop))

        bound = conflict = still_orphan = 0
        for c in rows:
            counterparty = c.to_number if (c.direction or "").startswith("out") else c.from_number
            shop_url, method = resolve_call(
                db, counterparty,
                evidence_table="calls", evidence_id=c.id,
                phone_to_shop=phone_to_shop,
            )
            if shop_url and shop_url != "conflict":
                c.shop_url = shop_url
                bound += 1
            elif shop_url == "conflict":
                conflict += 1
            else:
                still_orphan += 1

        if args.dry_run:
            db.rollback()
            log.info("dry-run: rolled back")
        else:
            db.commit()
            log.info("committed")

        log.info("bound=%d conflict=%d still_orphan=%d", bound, conflict, still_orphan)
    finally:
        db.close()


if __name__ == "__main__":
    main()
