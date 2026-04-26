"""Load Fireflies meetings into the meetings + meeting_attendees tables.

Sources:
  - meetings_raw.json          full transcript dump (has summaries, audio/video URLs)
  - meetings_with_links.json   thinner dump that contains meeting_link per id
  - meetlinkstoshopUrl (1).csv content from internal WA channels — joins meet_link -> shopUrl
  - emails_to_clients.csv      email -> shopUrl reverse lookup (built by join.py)

Mapping precedence: link-based, then email-based, then None (orphan).
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
from crm_app.models import Meeting, MeetingAttendee  # noqa: E402

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


def _build_email_to_shop():
    if not os.path.exists(EMAILS_PATH):
        print(f"  (skip) {EMAILS_PATH} not found — no email mappings")
        return {}
    out = {}
    with open(EMAILS_PATH, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            email = (row.get("email") or "").strip().lower()
            shop = (row.get("shopUrl") or "").strip().lower()
            if email and shop and email not in out:
                out[email] = shop
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


def _resolve_shop(meeting, link, link_to_shop, email_to_shop):
    if link and link in link_to_shop:
        return link_to_shop[link], "link"
    for att in meeting.get("meeting_attendees") or []:
        em = (att.get("email") or "").strip().lower()
        if em and not _is_internal(em) and em in email_to_shop:
            return email_to_shop[em], "email"
    return None, None


def load_fireflies():
    Base.metadata.create_all(bind=engine)
    if not os.path.exists(RAW_PATH):
        print(f"meetings_raw.json not found at {RAW_PATH}; aborting.")
        return

    print("Building link->shop and email->shop mappings...")
    link_to_shop = _build_link_to_shop()
    email_to_shop = _build_email_to_shop()
    id_to_link = _build_id_to_link()
    print(f"  {len(link_to_shop)} meet-links -> shop, "
          f"{len(email_to_shop)} emails -> shop, "
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
    orphans = 0

    try:
        for m in meetings:
            mid = m.get("id")
            if not mid:
                continue
            link = id_to_link.get(mid) or (m.get("meeting_link") or "").strip().lower() or None
            shop_url, mapping_source = _resolve_shop(m, link, link_to_shop, email_to_shop)
            if mapping_source == "link":
                mapped_link += 1
            elif mapping_source == "email":
                mapped_email += 1
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
        print(f"  orphans (no shop):  {orphans}")
    finally:
        db.close()


if __name__ == "__main__":
    load_fireflies()
