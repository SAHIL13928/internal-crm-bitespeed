"""Extract calendar invites from WhatsApp message bodies and create
future-dated `Meeting` rows for the ones that haven't happened yet.

Why this works: when someone sends a Google Calendar invite into a WA
chat, the invite text follows a stable shape:

    <title line e.g. "Acme <> Bitespeed | Weekly">
    <date line e.g. "Tuesday, 27 January · 3:30 – 4:30pm">
    Time zone: Asia/Kolkata
    Google Meet joining info
    Video call link: https://meet.google.com/<id>

We regex these blocks, parse the date with the message timestamp's year
as the default (calendar invites omit the year), filter to "future or
recently future" relative to NOW, and dedupe by meeting_link.

Each new Meeting row is bound to the merchant whose chat the invite
landed in (via the existing group_name → shop_url binding from
WhatsAppGroup). We mark `mapping_source='wa_upcoming_invite'` so
downstream tooling can tell these apart from Fireflies-imported rows.

Idempotent: keyed by `wa-upcoming:<meet_id>:<isoformat-date>` so the
same invite extracted twice → same row.

Usage:
    python scripts/extract_upcoming_meetings_from_wa.py --dry-run
    python scripts/extract_upcoming_meetings_from_wa.py
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from datetime import datetime, timedelta

from dateutil import parser as dateparser

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm_app.db import SessionLocal  # noqa: E402
from crm_app.models import Meeting, WhatsAppGroup, WhatsAppRawMessage  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("extract_upcoming_meetings")

# Pattern in calendar invites the team typically pastes. Captures the date
# line (e.g. "Tuesday, 27 January" / "Friday, 30 January") + time range.
# Year is intentionally absent — invites come without one.
DATE_LINE_RE = re.compile(
    r"""
    (?:Mon|Tues|Wed|Thurs|Fri|Sat|Sun)[a-z]*,?      # weekday
    \s+
    (?:\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*  # 27 January
      |(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2})  # January 27
    \s*[·\-–—]?\s*
    \d{1,2}:\d{2}\s*[–\-]\s*\d{1,2}:\d{2}\s*[ap]m   # 3:30 – 4:30pm
    """,
    re.VERBOSE | re.IGNORECASE,
)

MEET_LINK_RE = re.compile(r"https://meet\.google\.com/([a-z0-9]+(?:-[a-z0-9]+)*)", re.IGNORECASE)

# Title: stand-alone line with "<>" or " x " near "Bitespeed". Loose.
TITLE_LINE_RE = re.compile(
    r"^([^\n]*?(?:<>|\bx\b|\bX\b).{0,80}?[Bb]ite[Ss]peed[^\n]*)$",
    re.MULTILINE,
)

# Anything earlier than now-7d is firmly past — don't re-create.
# Anything within +/-3 days is borderline; we accept it (recurring meetings
# whose date was for "today" but invite came in this morning).
LOOKBACK_TOLERANCE = timedelta(days=3)


def _parse_invite(body: str, message_ts: datetime) -> list[dict]:
    """Return zero-or-more {title, when, link, meet_id} dicts."""
    out = []
    for link_match in MEET_LINK_RE.finditer(body):
        link = link_match.group(0).lower().rstrip("/")
        meet_id = link_match.group(1).lower()

        # Look in a window of ~600 chars before the link for the date line.
        window_start = max(0, link_match.start() - 600)
        window = body[window_start:link_match.start()]

        date_match = DATE_LINE_RE.search(window)
        if not date_match:
            continue

        date_str = date_match.group(0).strip().rstrip(".,")
        # Normalize unicode dashes/dots so dateutil parses cleanly.
        date_str = (date_str
                    .replace("·", " ")
                    .replace("–", "-")
                    .replace("—", "-"))
        # dateutil can't parse a time RANGE like "3:30 - 4:30pm". Drop
        # the end-time portion. We must propagate the am/pm from the
        # end time onto the start time (calendar invites only print
        # am/pm once, on the end). Use no-word-boundary matching since
        # "4:30pm" has no space before "pm".
        ampm_match = re.search(r"([ap]m)", date_str, re.IGNORECASE)
        ampm = ampm_match.group(1).lower() if ampm_match else ""
        m_dash = re.search(r"(\d{1,2}:\d{2})\s*-\s*\d{1,2}:\d{2}", date_str)
        if m_dash:
            date_str = date_str[:m_dash.end(1)] + (" " + ampm if ampm else "")

        # Use the message year as default. Calendar invites pasted in WA
        # always omit the year, so dateutil would otherwise default to
        # current year — wrong when the message is from a previous year.
        try:
            when = dateparser.parse(
                date_str,
                default=datetime(message_ts.year, message_ts.month, 1, 0, 0),
                fuzzy=True,
            )
        except (ValueError, OverflowError):
            continue

        # Year correction: invites for "Friday, 5 December" sent in
        # February of next year would parse as same-year February 5.
        # If parsed date is more than 30 days BEFORE the message
        # timestamp, assume it was meant for the following year.
        if (message_ts - when) > timedelta(days=30):
            try:
                when = when.replace(year=when.year + 1)
            except ValueError:
                pass

        # Title — best-effort: the line directly above the link block, or
        # any "X <> Bitespeed" line in the same window.
        title = None
        title_match = TITLE_LINE_RE.search(window[-500:] if len(window) > 500 else window)
        if title_match:
            title = title_match.group(1).strip()
        else:
            # Fallback: first non-blank non-noise line in the window.
            for ln in window.split("\n"):
                ln = ln.strip()
                if not ln or ln.startswith("Time zone") or ln.startswith("Google Meet") or ln.startswith("Or dial"):
                    continue
                title = ln[:200]
                break

        out.append({"title": title, "when": when, "link": link, "meet_id": meet_id})
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    now = datetime.utcnow()
    cutoff_floor = now - LOOKBACK_TOLERANCE

    db = SessionLocal()
    try:
        # Pre-cache: group_name → shop_url so we can attribute each invite
        # without a per-row DB hit.
        group_to_shop = {}
        for gn, shop in db.query(WhatsAppGroup.group_name, WhatsAppGroup.shop_url).all():
            if gn and shop and gn not in group_to_shop:
                group_to_shop[gn] = shop
        log.info("group→shop cache: %d entries", len(group_to_shop))

        # Pre-cache: existing meeting_link → meeting_id so we don't
        # double-insert invites already imported from Fireflies.
        existing_links = {
            (link.lower().rstrip("/") if link else None): mid
            for mid, link in db.query(Meeting.id, Meeting.meeting_link).all()
            if link
        }

        # Walk every WA message that mentions a meet link AND a "Time zone"
        # marker (which Google Calendar always emits). Avoid loading bodies
        # that are just an inline link mention.
        rows = (
            db.query(
                WhatsAppRawMessage.id, WhatsAppRawMessage.body,
                WhatsAppRawMessage.timestamp, WhatsAppRawMessage.group_name,
            )
            .filter(WhatsAppRawMessage.body.like("%meet.google.com%"))
            .filter(WhatsAppRawMessage.body.like("%Time zone%"))
            .all()
        )
        log.info("candidate messages: %d", len(rows))

        seen_invites: dict[str, dict] = {}  # dedupe key → invite dict + shop
        skipped_past = skipped_no_shop = 0

        for mid, body, ts, group_name in rows:
            if not body or not ts:
                continue
            for inv in _parse_invite(body, ts):
                if inv["when"] < cutoff_floor:
                    skipped_past += 1
                    continue
                shop_url = group_to_shop.get(group_name) if group_name else None
                if not shop_url:
                    skipped_no_shop += 1
                    continue

                # Dedupe key: same meet link + same scheduled date = same
                # logical meeting. (A weekly recurring invite produces a
                # new link each week, so they won't collide.)
                key = f"{inv['meet_id']}::{inv['when'].date().isoformat()}"
                if key in seen_invites:
                    # If we already saw the same invite, prefer the
                    # earliest message timestamp as the "scheduled-on".
                    continue
                inv["shop_url"] = shop_url
                inv["group_name"] = group_name
                inv["seen_at"] = ts
                seen_invites[key] = inv

        log.info(
            "parsed invites: %d  (skipped past: %d, no shop binding: %d)",
            len(seen_invites), skipped_past, skipped_no_shop,
        )

        # Insert / upsert
        inserted = updated = skipped_existing_link = 0
        for key, inv in seen_invites.items():
            existing_mid = existing_links.get(inv["link"])
            if existing_mid:
                # Already in Fireflies; refresh date if it's a future-dated
                # entry (Fireflies sometimes records meetings that haven't
                # been recorded yet with NULL date). Don't overwrite real
                # date unless it was None.
                m = db.get(Meeting, existing_mid)
                if m is not None and m.date is None:
                    m.date = inv["when"]
                    updated += 1
                else:
                    skipped_existing_link += 1
                continue

            mid = f"wa-upcoming:{inv['meet_id']}:{inv['when'].date().isoformat()}"
            existing = db.get(Meeting, mid)
            if existing:
                if existing.shop_url is None:
                    existing.shop_url = inv["shop_url"]
                if existing.date is None:
                    existing.date = inv["when"]
                continue

            db.add(Meeting(
                id=mid,
                shop_url=inv["shop_url"],
                title=inv["title"] or inv["group_name"] or "(scheduled meeting)",
                date=inv["when"],
                meeting_link=inv["link"],
                mapping_source="wa_upcoming_invite",
            ))
            inserted += 1

        if args.dry_run:
            db.rollback()
            log.info("dry-run — rolled back")
        else:
            db.commit()
            log.info("committed")

        log.info(
            "inserted=%d  updated=%d  skipped_existing_link=%d",
            inserted, updated, skipped_existing_link,
        )

        # Quick sample of what got inserted in the next 7 days
        if not args.dry_run:
            seven = now + timedelta(days=7)
            up = (
                db.query(Meeting)
                .filter(Meeting.date.between(now, seven), Meeting.shop_url.isnot(None))
                .order_by(Meeting.date)
                .limit(10).all()
            )
            log.info("---- sample of upcoming-7d meetings ----")
            for m in up:
                log.info("  %s  %s  %s", m.date, m.shop_url, (m.title or "")[:60])
    finally:
        db.close()


if __name__ == "__main__":
    main()
