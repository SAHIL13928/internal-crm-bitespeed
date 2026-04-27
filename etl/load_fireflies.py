"""Load Fireflies meetings into the meetings + meeting_attendees tables.

Sources:
  - meetings_raw.json          full transcript dump (has summaries, audio/video URLs)
  - meetings_with_links.json   thinner dump that contains meeting_link per id
  - meetlinkstoshopUrl (1).csv content from internal WA channels — joins meet_link -> shopUrl
  - contacts table              email -> shopUrl reverse lookup (built from master CSV)
  - emails_to_clients.csv       legacy fallback — older derived map
  - shops.shop_url / brand_name domain-based last-resort fallback (e.g. @acme.com -> acme.myshopify.com)

Mapping precedence: link → contact-email exact → legacy CSV email exact →
                    domain → None (orphan).
"""
import csv
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm_app.db import SessionLocal, engine, Base  # noqa: E402
from crm_app.models import Contact, Meeting, MeetingAttendee, Shop  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INPUTS = os.path.join(ROOT, "data", "inputs")
RAW_PATH = os.path.join(INPUTS, "meetings_raw.json")
LINKS_PATH = os.path.join(INPUTS, "meetings_with_links.json")
ARINDAM_PATH = os.path.join(INPUTS, "meetlinkstoshopUrl (1).csv")
EMAILS_PATH = os.path.join(INPUTS, "emails_to_clients.csv")

MEET_RE = re.compile(r"https://meet\.google\.com/[a-z0-9\-]+", re.IGNORECASE)
INTERNAL_DOMAINS = {"bitespeed.co"}


def _is_internal(email: str) -> bool:
    return bool(email) and email.lower().split("@")[-1] in INTERNAL_DOMAINS


def _build_link_to_shop():
    counts = defaultdict(lambda: defaultdict(int))
    if not os.path.exists(ARINDAM_PATH):
        print(f"  (skip) {ARINDAM_PATH} not found — no link mappings")
        return {}
    with open(ARINDAM_PATH, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            shop = (row.get("shopUrl") or "").strip().lower()
            content = row.get("content") or ""
            if not shop:
                continue
            for link in MEET_RE.findall(content):
                counts[link.lower()][shop] += 1
    out = {}
    for link, c in counts.items():
        out[link] = max(c.items(), key=lambda kv: kv[1])[0]
    return out


def _build_email_to_shop_from_contacts(db):
    """Rebuild email -> shop from the contacts table (the merged 1,300+ external
    contacts the master CSV produced). This dwarfs the legacy emails_to_clients.csv
    (which had ~734 rows). Bitespeed-internal emails are excluded since they
    can't disambiguate merchants."""
    out = {}
    rows = (
        db.query(Contact.email, Contact.shop_url)
        .filter(
            Contact.email.isnot(None),
            Contact.shop_url.isnot(None),
            Contact.is_internal.is_(False),
        )
        .all()
    )
    for email, shop in rows:
        e = (email or "").strip().lower()
        s = (shop or "").strip().lower()
        if e and s and e not in out:
            out[e] = s
    return out


def _build_email_to_shop_csv_fallback():
    """Legacy emails_to_clients.csv as a fallback — used to fill gaps the
    contacts table might miss."""
    if not os.path.exists(EMAILS_PATH):
        return {}
    out = {}
    with open(EMAILS_PATH, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            email = (row.get("email") or "").strip().lower()
            shop = (row.get("shopUrl") or "").strip().lower()
            if email and shop and email not in out:
                out[email] = shop
    return out


def _build_domain_to_shop(db):
    """Last-resort fallback: extract a brandable token from each shop_url
    (e.g. 'acme.myshopify.com' -> 'acme') and map plausible email domains
    onto the shop. Excludes generic webmail domains so noise doesn't pollute.

    Two strategies:
      1. domain prefix == shop token (e.g. 'acme.com' for shop 'acme.myshopify.com')
      2. brand_name (when populated) lowercased and slug-matched to email-domain prefix

    Conflicts (>1 shop matches the same domain) are dropped — orphan is
    safer than wrong-shop binding.
    """
    GENERIC = {
        "gmail.com", "yahoo.com", "yahoo.in", "yahoo.co.in", "outlook.com",
        "hotmail.com", "icloud.com", "live.com", "rediffmail.com",
        "googlemail.com", "ymail.com", "protonmail.com", "aol.com",
        "msn.com", "bitespeed.co",
    }
    SHOPIFY_SUFFIXES = (".myshopify.com",)

    counts = defaultdict(lambda: defaultdict(int))

    for shop_url, brand in db.query(Shop.shop_url, Shop.brand_name).all():
        s = (shop_url or "").lower()
        # token = shop_url stripped of myshopify suffix → 'acme.myshopify.com' -> 'acme'
        token = s
        for suf in SHOPIFY_SUFFIXES:
            if token.endswith(suf):
                token = token[: -len(suf)]
                break
        token = token.split(".")[0].strip()
        if token and len(token) >= 3:
            # plausible email domain candidates derived from token
            for tld in ("com", "in", "co.in", "co", "io", "shop", "store"):
                counts[f"{token}.{tld}"][s] += 1
        if brand:
            slug = "".join(c for c in brand.lower() if c.isalnum())
            if slug and len(slug) >= 3:
                for tld in ("com", "in", "co.in", "co", "io"):
                    counts[f"{slug}.{tld}"][s] += 1

    out = {}
    for domain, shops in counts.items():
        if domain in GENERIC:
            continue
        if len(shops) == 1:
            out[domain] = next(iter(shops.keys()))
        # else: ambiguous — drop, don't guess
    return out


def _build_id_to_link():
    if not os.path.exists(LINKS_PATH):
        print(f"  (skip) {LINKS_PATH} not found — no id->link map")
        return {}
    with open(LINKS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    out = {}
    for m in data:
        mid = m.get("id")
        link = (m.get("meeting_link") or "").strip().lower()
        if mid and link:
            out[mid] = link
    return out


def _ms_to_dt(ms):
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def _resolve_shop(meeting, link, link_to_shop, email_to_shop, domain_to_shop):
    """Precedence: link → external attendee email exact → domain fallback.
    Returns (shop_url, mapping_source). mapping_source is one of:
    'link', 'email', 'domain', or None for orphans."""
    if link and link in link_to_shop:
        return link_to_shop[link], "link"
    # Pass 1 — exact email match
    for att in meeting.get("meeting_attendees") or []:
        em = (att.get("email") or "").strip().lower()
        if em and not _is_internal(em) and em in email_to_shop:
            return email_to_shop[em], "email"
    # Pass 2 — domain fallback (only after no exact match)
    for att in meeting.get("meeting_attendees") or []:
        em = (att.get("email") or "").strip().lower()
        if not em or _is_internal(em):
            continue
        domain = em.split("@", 1)[-1]
        if domain in domain_to_shop:
            return domain_to_shop[domain], "domain"
    return None, None


def load_fireflies():
    Base.metadata.create_all(bind=engine)
    if not os.path.exists(RAW_PATH):
        print(f"meetings_raw.json not found at {RAW_PATH}; aborting.")
        return

    print("Building link->shop and email->shop mappings...")
    link_to_shop = _build_link_to_shop()
    id_to_link = _build_id_to_link()
    # email_to_shop and domain_to_shop need a DB session
    _setup_db = SessionLocal()
    try:
        email_to_shop = _build_email_to_shop_from_contacts(_setup_db)
        # Backfill from legacy CSV for anything contacts missed
        for k, v in _build_email_to_shop_csv_fallback().items():
            email_to_shop.setdefault(k, v)
        domain_to_shop = _build_domain_to_shop(_setup_db)
    finally:
        _setup_db.close()
    print(f"  {len(link_to_shop)} meet-links -> shop, "
          f"{len(email_to_shop)} emails -> shop, "
          f"{len(domain_to_shop)} domains -> shop, "
          f"{len(id_to_link)} meeting-ids -> link")

    print(f"Loading {RAW_PATH}...")
    with open(RAW_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    # The raw payload may be either {"data":{"transcripts":[...]}} or a flat list
    if isinstance(data, dict):
        meetings = data.get("data", {}).get("transcripts") or data.get("transcripts") or []
    else:
        meetings = data
    print(f"  {len(meetings)} meetings to process")

    # Dedupe by id, preferring the record with the richest summary (the file
    # is sometimes the concatenation of an initial fetch and a refetch pass).
    def _richness(m):
        s = m.get("summary") or {}
        return sum(1 for k in ("short_summary", "overview", "bullet_gist", "action_items", "keywords") if isinstance(s, dict) and s.get(k))

    by_id = {}
    for m in meetings:
        mid = m.get("id")
        if not mid:
            continue
        if mid not in by_id or _richness(m) > _richness(by_id[mid]):
            by_id[mid] = m
    if len(by_id) != len(meetings):
        print(f"  deduped to {len(by_id)} unique meeting ids")
    meetings = list(by_id.values())

    db = SessionLocal()
    inserted = 0
    updated = 0
    mapped_link = 0
    mapped_email = 0
    mapped_domain = 0
    orphans = 0

    try:
        for m in meetings:
            mid = m.get("id")
            if not mid:
                continue
            link = id_to_link.get(mid) or (m.get("meeting_link") or "").strip().lower() or None
            shop_url, mapping_source = _resolve_shop(m, link, link_to_shop, email_to_shop, domain_to_shop)
            if mapping_source == "link":
                mapped_link += 1
            elif mapping_source == "email":
                mapped_email += 1
            elif mapping_source == "domain":
                mapped_domain += 1
            else:
                orphans += 1

            summary = m.get("summary") or {}
            keywords = summary.get("keywords") if isinstance(summary, dict) else None

            existing = db.get(Meeting, mid)
            if existing is None:
                meeting = Meeting(id=mid)
                db.add(meeting)
                inserted += 1
            else:
                meeting = existing
                # wipe attendees so we can re-attach
                for a in list(meeting.attendees):
                    db.delete(a)
                updated += 1

            meeting.shop_url = shop_url
            meeting.title = m.get("title") or ""
            meeting.date = _ms_to_dt(m.get("date"))
            meeting.duration_min = m.get("duration")
            meeting.organizer_email = m.get("organizer_email")
            meeting.host_email = m.get("host_email")
            meeting.meeting_link = link
            meeting.transcript_url = m.get("transcript_url")
            meeting.audio_url = m.get("audio_url")
            meeting.video_url = m.get("video_url")
            if isinstance(summary, dict):
                meeting.summary_short = summary.get("short_summary")
                meeting.summary_overview = summary.get("overview")
                meeting.summary_bullet_gist = summary.get("bullet_gist")
                meeting.summary_keywords = json.dumps(keywords) if keywords else None
                meeting.action_items = summary.get("action_items")
            else:
                meeting.summary_short = None
                meeting.summary_overview = None
                meeting.summary_bullet_gist = None
                meeting.summary_keywords = None
                meeting.action_items = None
            meeting.mapping_source = mapping_source

            for att in m.get("meeting_attendees") or []:
                em = (att.get("email") or "").strip().lower() or None
                db.add(MeetingAttendee(
                    meeting_id=mid,
                    email=em,
                    display_name=att.get("displayName") or att.get("name"),
                    is_internal=_is_internal(em or ""),
                ))

            if inserted % 200 == 0 and inserted > 0:
                db.commit()

        db.commit()
        print(f"meetings inserted:    {inserted}")
        print(f"meetings updated:     {updated}")
        print(f"  mapped via link:    {mapped_link}")
        print(f"  mapped via email:   {mapped_email}")
        print(f"  mapped via domain:  {mapped_domain}")
        print(f"  orphans (no shop):  {orphans}")
    finally:
        db.close()


if __name__ == "__main__":
    load_fireflies()
