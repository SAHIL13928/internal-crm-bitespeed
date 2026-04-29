"""Extract Google Meet links from WhatsApp message bodies and use them
to bind chats → merchants. The original mapping insight from
data/inputs/meetlinkstoshopUrl (1).csv: every CS merchant chat tends to
have a meet link in it (calendar invites, reschedules, "join here"
messages), and we already have a curated link → shop_url table.

This script is the WA-side complement of `etl/load_fireflies.py`
(which uses the same link → shop map for meeting attribution). One meet
link in any message in a chat is high-confidence evidence that the
ENTIRE chat (and all its messages) belongs to that merchant.

What it does:
  1. Build the link → shop_url map from the curated CSV
     (`_build_link_to_shop()` reused from load_fireflies, so behavior
     cannot diverge with the meeting loader).
  2. Walk every whatsapp_raw_messages row, regex meet links from body.
  3. For each matched (link, shop) pair:
     • upsert WhatsAppGroup.shop_url for the chat's group (group_name)
     • add an identity-graph binding (group_name, shop_url) sourced
       from "meet_link_in_message"
     • mark every PENDING message in that group as resolved
  4. Log a summary.

Idempotent: re-running yields the same bindings (add_binding skips
dupes by natural key).

Usage:
    python scripts/extract_meetlinks_from_messages.py --dry-run
    python scripts/extract_meetlinks_from_messages.py
"""
import argparse
import logging
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import or_
from sqlalchemy.orm import Session  # noqa: E402

from crm_app.db import SessionLocal  # noqa: E402
from crm_app.identity import add_binding  # noqa: E402
from crm_app.models import WhatsAppGroup, WhatsAppRawMessage  # noqa: E402
from etl.load_fireflies import _build_link_to_shop, MEET_RE  # noqa: E402

# Also extract Zoom and other common meeting providers — we don't have
# curated mappings for these but logging them tells us what we're missing.
ZOOM_RE = re.compile(r"https://[a-z0-9\-]+\.?zoom\.us/j/\d+", re.IGNORECASE)
TEAMS_RE = re.compile(r"https://teams\.microsoft\.com/l/meetup-join/[^\s]+", re.IGNORECASE)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("extract_meetlinks")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    link_to_shop = _build_link_to_shop()
    log.info("link → shop map: %d entries", len(link_to_shop))
    if not link_to_shop:
        log.error("no link → shop map; nothing to do")
        return

    db: Session = SessionLocal()
    try:
        # Walk all messages with non-empty bodies. Sqlite full-text matching
        # 'meet.google.com' as a substring filter avoids loading all 600k+
        # bodies into Python.
        candidates = (
            db.query(
                WhatsAppRawMessage.id, WhatsAppRawMessage.group_name,
                WhatsAppRawMessage.body, WhatsAppRawMessage.sender_phone,
            )
            .filter(WhatsAppRawMessage.body.like("%meet.google.com%"))
            .all()
        )
        log.info("messages mentioning meet.google.com: %d", len(candidates))

        # group_name -> {shop_url: hit_count} so ties get logged
        group_to_shop_hits: dict[str, dict[str, int]] = {}
        zoom_messages = 0
        teams_messages = 0

        for mid, group_name, body, sender_phone in candidates:
            if not body or not group_name:
                continue
            for raw_link in MEET_RE.findall(body):
                link = raw_link.lower().rstrip("/")
                shop = link_to_shop.get(link)
                if not shop:
                    continue
                bucket = group_to_shop_hits.setdefault(group_name, {})
                bucket[shop] = bucket.get(shop, 0) + 1

        # Also count zoom/teams messages so we know the gap
        for _mid, _gn, body, _sp in candidates:
            if body and ZOOM_RE.search(body):
                zoom_messages += 1
            if body and TEAMS_RE.search(body):
                teams_messages += 1
        # We didn't pre-filter for those — re-scan everything for them.
        if not zoom_messages and not teams_messages:
            zoom_messages = (
                db.query(WhatsAppRawMessage)
                .filter(WhatsAppRawMessage.body.like("%zoom.us/j/%"))
                .count()
            )
            teams_messages = (
                db.query(WhatsAppRawMessage)
                .filter(WhatsAppRawMessage.body.like("%teams.microsoft.com/l/meetup-join%"))
                .count()
            )

        log.info("groups with at least one meet-link match: %d",
                 len(group_to_shop_hits))
        log.info("zoom-link messages (uncovered today): %d", zoom_messages)
        log.info("teams-link messages (uncovered today): %d", teams_messages)

        # Now decide a single shop per group: if a group has multiple
        # candidate shops (e.g. an old meet link from a different merchant
        # got forwarded), pick the one with the most hits. Tie → skip,
        # surface as ambiguous.
        bound_groups = ambiguous = 0
        message_resolutions = 0
        for group_name, hits in group_to_shop_hits.items():
            top_count = max(hits.values())
            top_shops = [s for s, c in hits.items() if c == top_count]
            if len(top_shops) > 1:
                ambiguous += 1
                log.debug("ambiguous group %r → %s", group_name, sorted(top_shops))
                continue
            shop_url = top_shops[0]

            # Update WhatsAppGroup row(s) for this group_name. There may
            # be multiple rows (one from static directory, one from a
            # backfilled chat_id JID); update all of them.
            for wag in db.query(WhatsAppGroup).filter_by(group_name=group_name).all():
                if wag.shop_url is None:
                    wag.shop_url = shop_url
            bound_groups += 1

            # Add identity-graph binding so resolver picks it up.
            try:
                add_binding(
                    db,
                    "group_name", group_name,
                    "shop_url", shop_url,
                    source="meet_link_in_message",
                    confidence=0.95,  # very high — meet link is a strong signal
                    evidence_table="whatsapp_raw_messages",
                    evidence_id=group_name,  # synthetic — group-scoped
                )
            except ValueError:
                pass

            # Mark all pending messages in this group as resolved.
            updated = (
                db.query(WhatsAppRawMessage)
                .filter(
                    WhatsAppRawMessage.group_name == group_name,
                    WhatsAppRawMessage.resolution_status == "pending",
                )
                .update(
                    {
                        "resolved_shop_url": shop_url,
                        "resolution_status": "resolved",
                        "resolution_method": "meet_link_in_message",
                        "processed_at": datetime.utcnow(),
                    },
                    synchronize_session=False,
                )
            )
            message_resolutions += updated

        if args.dry_run:
            db.rollback()
            log.info("dry-run — rolled back")
        else:
            db.commit()
            log.info("committed")

        log.info(
            "groups bound: %d  ambiguous: %d  messages newly resolved: %d",
            bound_groups, ambiguous, message_resolutions,
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
