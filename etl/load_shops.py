"""Load shopurl + number + emailids.csv into shops + contacts + whatsapp_groups."""
import csv
import os
import re
import sys

# Make repo root importable when run as a script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm_app.db import SessionLocal, engine, Base  # noqa: E402
from crm_app.models import Contact, Shop, WhatsAppGroup  # noqa: E402

CSV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "inputs", "shopurl + number + emailids.csv")

INTERNAL_DOMAINS = {"bitespeed.co"}
PHONE_LIKE = re.compile(r"^\+?\d{10,}$")


def _split(s):
    if not s:
        return []
    return [p.strip() for p in s.split(";") if p.strip()]


def _is_internal_email(email: str) -> bool:
    return email.lower().split("@")[-1] in INTERNAL_DOMAINS


def _looks_like_phone(s: str) -> bool:
    return bool(PHONE_LIKE.match(s.replace(" ", "")))


def load_shops():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        with open(CSV_PATH, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        shops_seen = 0
        contacts_added = 0
        groups_added = 0

        for row in rows:
            shop_url = (row.get("shopUrl") or "").strip().lower()
            if not shop_url:
                continue

            shop = db.get(Shop, shop_url)
            if shop is None:
                shop = Shop(
                    shop_url=shop_url,
                    confidence=(row.get("confidence") or "").strip() or None,
                )
                db.add(shop)
            else:
                # wipe existing children before re-loading (idempotent reload)
                for c in list(shop.contacts):
                    db.delete(c)
                for g in list(shop.whatsapp_groups):
                    db.delete(g)
                shop.confidence = (row.get("confidence") or "").strip() or None
            shops_seen += 1

            # ── Contacts: pair phones with contact_names by position ───────
            phones = _split(row.get("phones"))
            names = _split(row.get("contact_names"))

            # Filter out "name" tokens that are actually duplicate phone numbers
            cleaned_names = [n for n in names if not _looks_like_phone(n)]

            for i, phone in enumerate(phones):
                name = None
                # try exact-index match first against cleaned names
                if i < len(cleaned_names):
                    name = cleaned_names[i]
                db.add(Contact(
                    shop_url=shop_url,
                    name=name,
                    phone=phone,
                ))
                contacts_added += 1

            # Any extra cleaned names that didn't pair with a phone
            for n in cleaned_names[len(phones):]:
                db.add(Contact(shop_url=shop_url, name=n))
                contacts_added += 1

            # External emails — usually merchant-side
            for em in _split(row.get("external_emails")):
                db.add(Contact(
                    shop_url=shop_url,
                    email=em.lower(),
                    is_internal=_is_internal_email(em),
                ))
                contacts_added += 1

            # Internal emails — bitespeed account managers handling this shop
            for em in _split(row.get("internal_emails")):
                db.add(Contact(
                    shop_url=shop_url,
                    email=em.lower(),
                    is_internal=True,
                    role="account_manager",
                ))
                contacts_added += 1

            # WhatsApp groups
            for g in _split(row.get("whatsapp_groups")):
                db.add(WhatsAppGroup(shop_url=shop_url, group_name=g))
                groups_added += 1

        db.commit()
        print(f"shops upserted:        {shops_seen}")
        print(f"contacts inserted:     {contacts_added}")
        print(f"whatsapp_groups added: {groups_added}")
    finally:
        db.close()


if __name__ == "__main__":
    load_shops()
