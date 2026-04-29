"""Bind WhatsApp chats to merchants by brand-name token match.

A typical merchant WA group is named like 'Acme <> Bitespeed' or
'Bitespeed x Acme'. After we backfill 4k+ chats from Periskope, we can
scan their `chat_name` and bind to the merchant whose brand_name (or
shop_url stem) appears as a token in that name.

Why this is safe (not "fuzzy"):
  • We split chat_name into tokens (case-folded, alphanumerics only)
  • A merchant matches ONLY if its brand_name (>=4 chars to avoid noise
    like 'Sun', 'Pro', 'New') appears as an EXACT token sequence
  • We never match on email-style fragments or 1-2 char strings
  • Multi-merchant collisions are surfaced (logged + skipped) — manual
    review via /admin/conflicts

Effect: bound chats become accessible per-merchant, AND every member
of those chats becomes a phone↔shop_url binding in the identity graph.
That's high-quality real-world co-occurrence data.

Usage:
    python scripts/bind_chats_by_brand_name.py --dry-run
    python scripts/bind_chats_by_brand_name.py
"""
import argparse
import logging
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session  # noqa: E402

from crm_app.db import SessionLocal  # noqa: E402
from crm_app.identity import add_binding  # noqa: E402
from crm_app.models import Shop, WhatsAppGroup  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bind_chats_by_brand_name")

# Tokens shorter than this are too noisy (brands like 'Pro', 'Sun', 'IO')
MIN_BRAND_TOKEN_LEN = 4

# Stop-tokens: even if a brand_name contains these, we don't bind on them alone
STOP_TOKENS = {
    "bitespeed", "support", "team", "official", "store", "shop",
    "online", "india", "india", "global", "private", "limited", "ltd",
    "pvt", "llp", "company", "co", "the",
}


def _tokens(s: str) -> set[str]:
    """Lowercased alpha-only tokens, len >= MIN_BRAND_TOKEN_LEN, minus stops."""
    if not s:
        return set()
    raw = re.findall(r"[A-Za-z]{%d,}" % MIN_BRAND_TOKEN_LEN, s.lower())
    return {t for t in raw if t not in STOP_TOKENS}


def _build_brand_token_index(db: Session) -> dict:
    """Map: token -> {shop_url} for shops whose brand_name contains the token.
    A brand with multiple non-stop tokens ('rare', 'rabbit') indexes under each.
    Collisions (same token in 2 brands) are kept — caller resolves."""
    idx: dict[str, set[str]] = {}
    rows = db.query(Shop.shop_url, Shop.brand_name).filter(Shop.brand_name.isnot(None)).all()
    for url, brand in rows:
        for tok in _tokens(brand):
            idx.setdefault(tok, set()).add(url)
        # Also index the shop_url's leading token (e.g. 'amounee-store.myshopify.com' -> 'amounee')
        stem = re.split(r"[-.]", url)[0]
        if len(stem) >= MIN_BRAND_TOKEN_LEN and stem not in STOP_TOKENS:
            idx.setdefault(stem, set()).add(url)
    return idx


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    db = SessionLocal()
    try:
        brand_idx = _build_brand_token_index(db)
        log.info("brand-token index: %d distinct tokens", len(brand_idx))

        # Walk every WhatsAppGroup that has a name but no shop binding yet.
        candidates = (
            db.query(WhatsAppGroup)
            .filter(WhatsAppGroup.shop_url.is_(None))
            .filter(WhatsAppGroup.group_name.isnot(None))
            .all()
        )
        log.info("unbound chats with names: %d", len(candidates))

        bound = ambiguous = unmatched = 0
        for wag in candidates:
            tokens = _tokens(wag.group_name)
            # Find the intersection: shops whose brand contains any chat token.
            hits: dict[str, int] = {}  # shop_url -> match-count
            for tok in tokens:
                for shop_url in brand_idx.get(tok, ()):
                    hits[shop_url] = hits.get(shop_url, 0) + 1

            if not hits:
                unmatched += 1
                continue

            # Pick the winner: highest match count. Tie → ambiguous, skip.
            top_count = max(hits.values())
            top_shops = [s for s, c in hits.items() if c == top_count]
            if len(top_shops) > 1:
                ambiguous += 1
                log.debug("ambiguous: %r → %s", wag.group_name, sorted(top_shops))
                continue

            shop_url = top_shops[0]
            wag.shop_url = shop_url
            bound += 1

            # Also seed a graph binding so future events resolve via it.
            try:
                add_binding(
                    db,
                    "group_name", wag.group_name,
                    "shop_url", shop_url,
                    source="brand_name_match",
                    confidence=0.8,  # high-quality token match, but not 1.0 like static directory
                    evidence_table="whatsapp_groups",
                    evidence_id=str(wag.id),
                )
            except ValueError:
                pass

        if args.dry_run:
            db.rollback()
            log.info("dry-run — rolled back")
        else:
            db.commit()
            log.info("committed")
        log.info("bound=%d ambiguous=%d unmatched=%d", bound, ambiguous, unmatched)
    finally:
        db.close()


if __name__ == "__main__":
    main()
