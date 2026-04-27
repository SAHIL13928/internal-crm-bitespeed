"""Re-seed the identity graph from the static directory.

For every shop in the database we emit:
  (phone,        shop_url)   from contacts.phone (where is_internal=False)
  (email,        shop_url)   from contacts.email (where is_internal=False)
  (group_name,   shop_url)   from whatsapp_groups.group_name

These are static_directory bindings — confidence 1.0. We never add edges
between a (phone, X) and a (group_name, Y) without a real co-occurrence;
those come from webhook traffic.

Idempotent: re-runs add nothing because add_binding skips dupes by
(a, b, source, evidence_id). Safe to run after editing the master CSV
and reloading shops.

Usage:
    python scripts/recompute_static_directory_bindings.py [--dry-run]
"""
import argparse
import logging
import os
import sys

# Make repo root importable when run as `python scripts/...`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm_app.db import Base, SessionLocal, engine  # noqa: E402
from crm_app.identity import add_binding  # noqa: E402
from crm_app.models import Contact, Shop, WhatsAppGroup  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("recompute_static_directory_bindings")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true",
                   help="compute bindings but roll back; nothing persisted")
    args = p.parse_args()

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        n_phone = n_email = n_group = 0

        # contacts.phone → shop_url (only merchant-side contacts)
        contact_rows = (
            db.query(Contact.phone, Contact.email, Contact.shop_url, Contact.is_internal, Contact.id)
            .filter(Contact.shop_url.isnot(None))
            .all()
        )
        for phone, email, shop_url, is_internal, cid in contact_rows:
            if is_internal:
                continue
            if phone:
                try:
                    add_binding(
                        db, "phone", phone, "shop_url", shop_url,
                        source="static_directory", confidence=1.0,
                        evidence_table="contacts", evidence_id=str(cid),
                    )
                    n_phone += 1
                except ValueError:
                    pass  # empty/invalid value
            if email:
                try:
                    add_binding(
                        db, "email", email, "shop_url", shop_url,
                        source="static_directory", confidence=1.0,
                        evidence_table="contacts", evidence_id=str(cid),
                    )
                    n_email += 1
                except ValueError:
                    pass

        # whatsapp_groups.group_name → shop_url
        group_rows = (
            db.query(WhatsAppGroup.group_name, WhatsAppGroup.shop_url, WhatsAppGroup.id)
            .filter(WhatsAppGroup.shop_url.isnot(None))
            .filter(WhatsAppGroup.group_name.isnot(None))
            .all()
        )
        for group_name, shop_url, gid in group_rows:
            try:
                add_binding(
                    db, "group_name", group_name, "shop_url", shop_url,
                    source="static_directory", confidence=1.0,
                    evidence_table="whatsapp_groups", evidence_id=str(gid),
                )
                n_group += 1
            except ValueError:
                pass

        log.info("phone bindings emitted:      %d", n_phone)
        log.info("email bindings emitted:      %d", n_email)
        log.info("group_name bindings emitted: %d", n_group)

        if args.dry_run:
            db.rollback()
            log.info("dry-run: rolled back")
        else:
            db.commit()
            log.info("committed")
    finally:
        db.close()


if __name__ == "__main__":
    main()
