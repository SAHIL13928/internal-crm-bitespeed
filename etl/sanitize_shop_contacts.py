"""Defensive cleanup of shop contacts + WhatsApp groups.

The upstream master CSV (`shopurl + number + emailids.csv`) was produced by a
fuzzy/token matcher that over-merges shops sharing common nouns ("clothing",
"factory", "shop"). This loader trusted the CSV blindly, so dashboards now
show ~19 wrong external contacts + ~23 wrong AMs + 5 wrong WA groups on
shops like `the-clothing-factory-shop.myshopify.com`.

This module re-cleans contacts + WhatsApp groups in place using:

  Distinctive-token index across shop slugs + brand_names
    - drops generic stopwords (clothing, factory, shop, …)
    - "distinctive" = length ≥ 6, appears in ≤ 2 shops globally, not stopword

  External email rules
    1. KEEP if email appears as Fireflies meeting attendee for THIS shop
    2. DROP if domain prefix attributes to a different shop (uniquely)
    3. KEEP if domain prefix matches a distinctive token of this shop
    4. KEEP if domain prefix concatenates 2+ tokens of this shop
       (e.g. 'theclothingfactoryshop.com' for 'the-clothing-factory-shop')
    5. KEEP if local part contains a distinctive shop token
    6. DROP generic webmail (gmail, yahoo, …) with no other evidence
    7. DROP unattributable

  Internal AM rules
    - KEEP if AM appears as internal attendee on this shop's mapped meetings
    - Otherwise hard cap at top-3 (alphabetical) — the CSV over-attaches AMs

  WhatsApp group rules
    - Parse brand prefix (split on <>, X, x, vs, &, |)
    - KEEP if brand exact-matches shop slug or brand_name
    - KEEP if brand shares a distinctive token with shop
    - KEEP if brand has 2+ token overlap with shop
      (handles all-stopword shops like "The Clothing Factory")
    - DROP otherwise

Phones are not modified — no per-phone attribution available.

Run:
    python -m etl.sanitize_shop_contacts                    # apply
    python -m etl.sanitize_shop_contacts --report-only      # dry diff
    python -m etl.sanitize_shop_contacts --shop URL         # one shop, full diff
"""
import argparse
import csv
import difflib
import logging
import os
import re
import sys
from collections import Counter, defaultdict
from typing import Optional, Set, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session  # noqa: E402

from crm_app.db import Base, SessionLocal, engine  # noqa: E402
from crm_app.models import Contact, Meeting, MeetingAttendee, Shop, WhatsAppGroup  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(ROOT, "data", "inputs", "shopurl + number + emailids.csv")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("sanitize")

# Common-noun tokens that should NEVER drive a shop match by themselves.
HARD_STOPWORDS = {
    # generic English
    "the", "and", "of", "for", "in", "to", "a", "an", "by", "on", "at", "with",
    "my", "your", "our", "his", "her", "new", "old", "x", "vs", "via",
    # platform / suffix tokens
    "co", "com", "in", "uk", "us", "io", "shop", "shops", "store", "stores", "online",
    "official", "global", "world", "india", "lifestyle", "products", "retail",
    "wholesale", "enterprises", "industries", "ventures", "international",
    # business-descriptor common nouns
    "factory", "factories", "fashion", "fashions", "clothing", "apparel",
    "wear", "wears", "outfits", "studio", "studios", "design", "designs", "designer",
    "beauty", "beauties", "skin", "skincare", "makeup", "cosmetics",
    "jewelry", "jewellery", "jewels",
    "home", "house", "homes", "decor", "kitchen", "kitchens", "garden",
    "health", "healthcare", "wellness", "fit", "fitness",
    "food", "foods", "bakery", "cafe", "coffee", "tea",
    "kids", "kid", "baby", "babies", "child", "children", "junior",
    "organic", "natural", "naturals", "pure", "essentials",
    "group", "groups", "team", "teams",
    # company suffixes that show up in WA group titles
    "bitespeed", "bytespeed",
}

GENERIC_DOMAINS = {
    "gmail.com", "yahoo.com", "yahoo.in", "yahoo.co.in", "outlook.com",
    "hotmail.com", "icloud.com", "live.com", "rediffmail.com",
    "googlemail.com", "ymail.com", "protonmail.com", "aol.com", "msn.com",
    "bitespeed.co",
}

PLATFORM_SUFFIXES = (".myshopify.com", ".shopify.com", ".bigcartel.com")
TLD_SUFFIXES = (".co.in", ".co.uk", ".com", ".in", ".io", ".co", ".net", ".org")
WORD_RE = re.compile(r"[a-z0-9]{3,}")
INTERNAL_AM_CAP = 3


def _all_tokens(s: str) -> Set[str]:
    """All tokens from a shop_url / brand_name. Lowercased, length >= 3.
    Stops at platform suffixes; splits on -, _, ., space."""
    if not s:
        return set()
    s = s.lower()
    for suf in PLATFORM_SUFFIXES + TLD_SUFFIXES:
        if s.endswith(suf):
            s = s[: -len(suf)]
            break
    s = re.sub(r"[-_.\s]+", " ", s)
    return set(WORD_RE.findall(s))


def _split(s):
    if not s:
        return []
    return [p.strip() for p in s.split(";") if p.strip()]


def _read_csv():
    out = {}
    with open(CSV_PATH, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            url = (row.get("shopUrl") or "").strip().lower()
            if url:
                out[url] = row
    return out


_BITESPEED_NAMES = {"bitespeed", "bytespeed"}


def _normcompact(s: str) -> str:
    """Lowercase + strip everything but a-z0-9. 'Curl Up' -> 'curlup'."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _parse_wa_brand(group_name: str) -> str:
    """Pull the merchant-side brand out of a WA group title. Handles both orders:
       'Acme <> Bitespeed' -> 'Acme'
       'Bitespeed <> Acme' -> 'Acme'
    """
    name = group_name.strip()
    lower = name.lower()
    seps = [
        " <> ", "<>", " x bitespeed", " x bytespeed",
        " | bitespeed", " | bytespeed", " vs bitespeed", " vs bytespeed",
        " x ", " | ", " & ", " + ", " vs ",
    ]
    for sep in seps:
        idx = lower.find(sep)
        if idx > 0:
            left = name[:idx].strip()
            right = name[idx + len(sep):].strip()
            # If either side is just "bitespeed", brand is the other
            if _normcompact(left) in _BITESPEED_NAMES and _normcompact(right) not in _BITESPEED_NAMES:
                return right
            if _normcompact(right) in _BITESPEED_NAMES and _normcompact(left) not in _BITESPEED_NAMES:
                return left
            return left  # default
    for suffix in [" bitespeed", " bytespeed"]:
        if lower.endswith(suffix):
            return name[: -len(suffix)].strip()
    # Standalone "Bitespeed Foo" prefix
    for prefix in ["bitespeed ", "bytespeed "]:
        if lower.startswith(prefix):
            return name[len(prefix):].strip()
    return name


def _classify_email(em, shop_url, my_tokens, my_distinct, domain_to_shop, evidence_set):
    em_l = em.lower().strip()
    if "@" not in em_l:
        return ("drop", "invalid email")
    local, domain = em_l.split("@", 1)
    domain_prefix = domain.split(".")[0]

    # 1. Definitive keep — appeared in this shop's mapped meeting attendees
    if em_l in evidence_set:
        return ("keep", "Fireflies attendee on this shop's meetings")

    # 2. Definitive drop — domain prefix uniquely belongs to a different shop
    other = domain_to_shop.get(domain_prefix)
    if other and other != shop_url:
        return ("drop", f"domain '{domain_prefix}' attributes to {other}")

    # 3. Strong keep — domain prefix is a distinctive token of THIS shop
    if domain_prefix in my_distinct:
        return ("keep", f"domain matches distinctive shop token '{domain_prefix}'")

    # 4. Strong keep — domain prefix concatenates 2+ tokens of this shop
    multi = [t for t in my_tokens if len(t) >= 4 and t in domain_prefix]
    if len(multi) >= 2:
        return ("keep", f"domain '{domain_prefix}' contains shop tokens {sorted(multi)}")

    # 5. Weak keep — local part contains a distinctive shop token
    in_local = [t for t in my_distinct if len(t) >= 5 and t in local]
    if in_local:
        return ("keep", f"local '{local}' contains '{in_local[0]}'")

    # 6. Drop — generic webmail with no evidence
    if domain in GENERIC_DOMAINS:
        return ("drop", "generic webmail, no other evidence")

    # 7. Drop — unattributable
    return ("drop", "domain unattributable")


def _classify_wa_group(g, shop_url, shop_brand, my_tokens, my_distinct):
    brand = _parse_wa_brand(g)
    brand_lower = brand.lower()
    brand_compact = _normcompact(brand)
    brand_tokens = set(WORD_RE.findall(brand_lower))

    shop_slug_compact = _normcompact(shop_url.split(".")[0])
    shop_brand_compact = _normcompact(shop_brand) if shop_brand else ""

    # 1. Exact match against slug or brand_name
    if brand_compact == shop_slug_compact:
        return ("keep", "brand == shop slug")
    if shop_brand_compact and brand_compact == shop_brand_compact:
        return ("keep", "brand == shop brand_name")

    # 2. Fuzzy near-match (handles 'BOHECO' vs 'bohecoo', 'Curl Up' vs 'curlupp')
    if brand_compact and len(brand_compact) >= 4:
        for target in (shop_slug_compact, shop_brand_compact):
            if not target or len(target) < 4:
                continue
            ratio = difflib.SequenceMatcher(None, brand_compact, target).ratio()
            # 0.85 lets 1-char-off names match (e.g. "boheco" vs "bohecoo" ≈ 0.92)
            if ratio >= 0.85:
                return ("keep", f"fuzzy ratio {ratio:.2f} vs '{target}'")

    # 3. Distinctive token overlap
    distinctive_overlap = brand_tokens & my_distinct
    if distinctive_overlap:
        return ("keep", f"distinctive overlap: {sorted(distinctive_overlap)}")

    # 4. Multi-token overlap (≥ 2) — handles shops where every token is a stopword
    all_overlap = brand_tokens & my_tokens
    if len(all_overlap) >= 2:
        return ("keep", f"{len(all_overlap)}-token overlap: {sorted(all_overlap)}")

    # 5. Drop
    return ("drop", f"brand '{brand}' has no meaningful overlap")


def _build_indices(rows, db):
    """Build the cross-shop token + domain index used by classifiers."""
    shop_tokens = {}
    for shop_url in rows:
        tokens = _all_tokens(shop_url)
        shop = db.get(Shop, shop_url)
        if shop and shop.brand_name:
            tokens |= _all_tokens(shop.brand_name)
        # filter stopwords for the "tokens" set (we keep raw for multi-overlap)
        shop_tokens[shop_url] = tokens

    # Token frequency across shops (for distinctive detection)
    freq = Counter()
    for ts in shop_tokens.values():
        for t in ts - HARD_STOPWORDS:
            freq[t] += 1

    # Distinctive tokens per shop: length ≥ 6, freq ≤ 2, not stopword
    shop_distinct = {
        url: {t for t in ts - HARD_STOPWORDS if len(t) >= 6 and freq[t] <= 2}
        for url, ts in shop_tokens.items()
    }

    # Domain prefix → shop (only when uniquely attributable via a distinctive token)
    domain_to_shop = {}
    conflicts = set()
    for url, ts in shop_distinct.items():
        for t in ts:
            if t in conflicts:
                continue
            if t in domain_to_shop and domain_to_shop[t] != url:
                conflicts.add(t)
                domain_to_shop.pop(t, None)
            else:
                domain_to_shop[t] = url

    return shop_tokens, shop_distinct, domain_to_shop


def _build_evidence(db):
    """Per-shop email evidence from Fireflies meeting attendees on link-mapped
    meetings. Email-mapped meetings are skipped because their mapping was driven
    by the contacts table we're trying to clean — circular."""
    ext = defaultdict(set)
    intl = defaultdict(set)
    rows = (
        db.query(Meeting.shop_url, MeetingAttendee.email, MeetingAttendee.is_internal)
        .join(MeetingAttendee, MeetingAttendee.meeting_id == Meeting.id)
        .filter(Meeting.shop_url.isnot(None))
        .filter(Meeting.mapping_source == "link")  # <-- only trust link-mapped
        .filter(MeetingAttendee.email.isnot(None))
        .all()
    )
    for shop_url, email, is_internal in rows:
        bucket = intl if is_internal else ext
        bucket[shop_url].add(email.lower())
    return ext, intl


def main(report_only: bool = False, target_shop: Optional[str] = None):
    Base.metadata.create_all(bind=engine)
    rows = _read_csv()
    logger.info("CSV rows: %d", len(rows))

    db = SessionLocal()
    try:
        shop_tokens, shop_distinct, domain_to_shop = _build_indices(rows, db)
        logger.info(
            "indices: %d shops; %d total distinctive tokens; %d unique domain-prefix → shop",
            len(rows),
            sum(len(s) for s in shop_distinct.values()),
            len(domain_to_shop),
        )

        evidence_ext, evidence_int = _build_evidence(db)
        logger.info(
            "Fireflies link-mapped evidence: %d shops with external attendees, %d with internal",
            len(evidence_ext), len(evidence_int),
        )

        target_iter = [target_shop] if target_shop else list(rows.keys())
        totals = Counter()
        diffs = []  # (shop_url, kept_ext, dropped_ext, kept_int, dropped_int, kept_g, dropped_g)

        for shop_url in target_iter:
            row = rows.get(shop_url)
            if not row:
                if target_shop:
                    logger.error("shop %s not in CSV", shop_url)
                continue

            shop = db.get(Shop, shop_url)
            shop_brand = (shop.brand_name if shop else None) or None
            my_tokens = shop_tokens[shop_url]
            my_distinct = shop_distinct[shop_url]
            ev_set = evidence_ext.get(shop_url, set())
            int_ev_set = evidence_int.get(shop_url, set())

            # ── External emails ──
            kept_ext, dropped_ext = [], []
            for em in _split(row.get("external_emails")):
                verdict, reason = _classify_email(em, shop_url, my_tokens, my_distinct, domain_to_shop, ev_set)
                (kept_ext if verdict == "keep" else dropped_ext).append((em.lower(), reason))

            # ── Internal AMs ──
            kept_int, dropped_int = [], []
            csv_int = sorted(_split(row.get("internal_emails")), key=str.lower)
            for em in csv_int:
                em_l = em.lower()
                if em_l in int_ev_set:
                    kept_int.append((em_l, "internal attendee on link-mapped meeting"))
                elif len([k for k, _ in kept_int]) < INTERNAL_AM_CAP:
                    # No meeting evidence — soft cap
                    kept_int.append((em_l, f"no meeting evidence; capped at {INTERNAL_AM_CAP}"))
                else:
                    dropped_int.append((em_l, f"capped (>{INTERNAL_AM_CAP}) and no meeting evidence"))

            # ── WhatsApp groups ──
            kept_g, dropped_g = [], []
            for g in _split(row.get("whatsapp_groups")):
                verdict, reason = _classify_wa_group(g, shop_url, shop_brand, my_tokens, my_distinct)
                (kept_g if verdict == "keep" else dropped_g).append((g, reason))

            totals["shops"] += 1
            totals["ext_kept"] += len(kept_ext); totals["ext_dropped"] += len(dropped_ext)
            totals["int_kept"] += len(kept_int); totals["int_dropped"] += len(dropped_int)
            totals["wa_kept"] += len(kept_g); totals["wa_dropped"] += len(dropped_g)

            diffs.append((shop_url, kept_ext, dropped_ext, kept_int, dropped_int, kept_g, dropped_g))

            if not report_only:
                # Wipe email contacts + WA groups; phones stay untouched
                db.query(Contact).filter(
                    Contact.shop_url == shop_url, Contact.email.isnot(None)
                ).delete(synchronize_session=False)
                db.query(WhatsAppGroup).filter(
                    WhatsAppGroup.shop_url == shop_url
                ).delete(synchronize_session=False)
                for em, _ in kept_ext:
                    db.add(Contact(shop_url=shop_url, email=em, is_internal=False))
                for em, _ in kept_int:
                    db.add(Contact(shop_url=shop_url, email=em, is_internal=True, role="account_manager"))
                for g, _ in kept_g:
                    db.add(WhatsAppGroup(shop_url=shop_url, group_name=g))

        if not report_only:
            db.commit()

        logger.info("=== summary ===")
        for k in ("shops", "ext_kept", "ext_dropped", "int_kept", "int_dropped", "wa_kept", "wa_dropped"):
            logger.info("  %-15s %d", k + ":", totals[k])

        if target_shop and diffs:
            for shop_url, ke, de, ki, di, kg, dg in diffs:
                print(f"\n========== {shop_url} ==========")
                print(f"\n-- EXTERNAL kept ({len(ke)}) --")
                for em, r in ke: print(f"  KEEP {em:55s}  -- {r}")
                print(f"\n-- EXTERNAL dropped ({len(de)}) --")
                for em, r in de: print(f"  DROP {em:55s}  -- {r}")
                print(f"\n-- INTERNAL kept ({len(ki)}) --")
                for em, r in ki: print(f"  KEEP {em:55s}  -- {r}")
                print(f"\n-- INTERNAL dropped ({len(di)}) --")
                for em, r in di: print(f"  DROP {em:55s}  -- {r}")
                print(f"\n-- WHATSAPP kept ({len(kg)}) --")
                for g, r in kg: print(f"  KEEP {g:55s}  -- {r}")
                print(f"\n-- WHATSAPP dropped ({len(dg)}) --")
                for g, r in dg: print(f"  DROP {g:55s}  -- {r}")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--shop", help="single shop_url to operate on")
    args = parser.parse_args()
    main(report_only=args.report_only, target_shop=args.shop)
