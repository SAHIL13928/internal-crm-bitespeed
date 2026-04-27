"""Walk WhatsAppRawMessage rows where resolution_status='pending' and
re-run resolution. Use this after seeding new bindings (e.g. after a
batch of FreJun calls grew the graph) to retroactively bind WA messages
we previously couldn't resolve.

Idempotent: messages that resolve get marked resolved + processed_at
updated. Messages still unresolved stay pending — we do NOT downgrade
to 'unresolvable' because new bindings may arrive later.

Usage:
    python scripts/reprocess_pending.py             # process every pending row
    python scripts/reprocess_pending.py --limit 500 # cap for sanity testing
    python scripts/reprocess_pending.py --dry-run
"""
import argparse
import logging
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm_app.db import Base, SessionLocal, engine  # noqa: E402
from crm_app.identity import CONFLICT  # noqa: E402
from crm_app.models import WhatsAppRawMessage  # noqa: E402
from crm_app.resolver import resolve_whatsapp_message  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("reprocess_pending")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None,
                   help="cap number of rows processed")
    p.add_argument("--dry-run", action="store_true",
                   help="compute resolutions but roll back")
    args = p.parse_args()

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        q = (
            db.query(WhatsAppRawMessage)
            .filter(WhatsAppRawMessage.resolution_status == "pending")
            .order_by(WhatsAppRawMessage.id)
        )
        if args.limit:
            q = q.limit(args.limit)
        rows = q.all()
        log.info("pending rows to revisit: %d", len(rows))

        resolved = conflict = still_pending = 0
        for r in rows:
            shop_url, method = resolve_whatsapp_message(
                db,
                sender_phone=r.sender_phone,
                group_name=r.group_name,
                evidence_table="whatsapp_raw_messages",
                evidence_id=str(r.id),
            )
            r.processed_at = datetime.utcnow()
            if shop_url and shop_url != CONFLICT:
                r.resolved_shop_url = shop_url
                r.resolution_status = "resolved"
                r.resolution_method = method
                resolved += 1
            elif shop_url == CONFLICT:
                r.resolution_status = "conflict"
                r.resolution_method = method
                conflict += 1
            else:
                # Stay pending — do not downgrade to unresolvable (spec).
                r.resolution_method = method
                still_pending += 1

        if args.dry_run:
            db.rollback()
            log.info("dry-run: rolled back")
        else:
            db.commit()
            log.info("committed")

        log.info("resolved=%d conflict=%d still_pending=%d",
                 resolved, conflict, still_pending)
    finally:
        db.close()


if __name__ == "__main__":
    main()
