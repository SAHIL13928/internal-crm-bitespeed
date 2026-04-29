"""Pull historical chats + messages from Periskope's REST API.

Webhooks only deliver new events from now forward; this script fills the
gap by paginating through Periskope's stored history. Same per-event
mapping the live webhook uses (`crm_app/webhooks/periskope.py`) so
behavior cannot diverge between live and backfill paths.

Endpoints:
  GET https://api.periskope.app/v1/chats            (list all chats)
  GET https://api.periskope.app/v1/chats/messages   (list all messages)

Auth:
  Authorization: Bearer <PERISKOPE_API_KEY>
  x-phone: <PERISKOPE_PHONE>          (org's WhatsApp number, no @c.us)

Pagination: offset + limit (default/max 2000). Sort: timestamp DESC.

Idempotent — re-running upserts via:
  • whatsapp_groups.group_jid (chat upsert)
  • whatsapp_raw_messages natural key + source_message_id

Bonus: every member of a chat that maps to a known merchant gets a
phone↔shop_url binding added to the identity graph. That's a real
co-occurrence (the person is in the merchant's WA group), so it grows
resolution coverage for downstream calls/messages.

Usage:
    python scripts/backfill_periskope.py                    # everything
    python scripts/backfill_periskope.py --since 2025-01-01 # filtered
    python scripts/backfill_periskope.py --max-pages 5      # cap for sanity
    python scripts/backfill_periskope.py --chats-only       # skip messages
    python scripts/backfill_periskope.py --dry-run          # roll back at end
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime
from typing import Iterator, Optional

import requests
from dotenv import dotenv_values

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Force load .env from repo root before importing crm_app modules.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for k, v in dotenv_values(os.path.join(_REPO_ROOT, ".env")).items():
    os.environ.setdefault(k, v)

from crm_app.db import Base, SessionLocal, engine  # noqa: E402
from crm_app.identity import add_binding  # noqa: E402
from crm_app.models import WhatsAppGroup, WhatsAppRawMessage  # noqa: E402
from crm_app.utils import build_phone_to_shop, norm_phone, to_naive_utc  # noqa: E402
from crm_app.webhooks.periskope import (  # noqa: E402
    _handle_chat_created,
    _handle_message_created,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("backfill_periskope")

API_BASE = "https://api.periskope.app/v1"
PAGE_SIZE = 2000  # Periskope's max
MAX_429_RETRIES = 6
HTTP_TIMEOUT = 90


def _headers() -> dict:
    api_key = os.environ.get("PERISKOPE_API_KEY")
    phone = os.environ.get("PERISKOPE_PHONE")
    if not api_key or not phone:
        log.error("PERISKOPE_API_KEY and PERISKOPE_PHONE must both be set in .env")
        sys.exit(2)
    return {"Authorization": f"Bearer {api_key}", "x-phone": phone}


def _get_with_retry(url: str, params: dict, headers: dict) -> dict:
    """GET with retry on 429s AND on connection errors.

    Periskope drops the socket on long-running paginated pulls (we hit
    `RemoteDisconnected: Remote end closed connection without response`
    around offset ~62k). Retry the request — pagination is idempotent."""
    for attempt in range(MAX_429_RETRIES):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=HTTP_TIMEOUT)
        except (requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError,
                requests.exceptions.ReadTimeout) as e:
            wait = 2 ** attempt
            log.warning("connection error (%s) — sleeping %ds (attempt %d/%d)",
                        type(e).__name__, wait, attempt + 1, MAX_429_RETRIES)
            time.sleep(wait)
            continue

        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            wait = int(retry_after) if retry_after and retry_after.isdigit() else (2 ** attempt)
            log.warning("429 — sleeping %ds (attempt %d/%d)", wait, attempt + 1, MAX_429_RETRIES)
            time.sleep(wait)
            continue
        if 500 <= resp.status_code < 600:
            wait = 2 ** attempt
            log.warning("5xx (%d) — sleeping %ds (attempt %d/%d)",
                        resp.status_code, wait, attempt + 1, MAX_429_RETRIES)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError(f"exceeded {MAX_429_RETRIES} retries at {url}")


def _paginate(path: str, item_key: str, params: dict, headers: dict,
              max_pages: Optional[int] = None, start_offset: int = 0) -> Iterator[list]:
    """Yield each page's items. Stops when a page returns less than PAGE_SIZE.
    Pass `start_offset` to resume after an interrupted run."""
    url = f"{API_BASE}{path}"
    offset = start_offset
    page_num = 0
    while True:
        page_num += 1
        if max_pages and page_num > max_pages:
            log.info("hit --max-pages=%d, stopping", max_pages)
            break
        page_params = {**params, "offset": offset, "limit": PAGE_SIZE}
        body = _get_with_retry(url, page_params, headers)
        items = body.get(item_key) or []
        log.info("page %d %s offset=%d → %d items (count=%s, to=%s)",
                 page_num, path, offset, len(items), body.get("count"), body.get("to"))
        if not items:
            break
        yield items
        if len(items) < PAGE_SIZE:
            break
        offset += PAGE_SIZE


# ── chat backfill ─────────────────────────────────────────────────────────
def _grow_graph_from_members(db, chat: dict, phone_to_shop: dict) -> int:
    """If this chat's group_name maps to a merchant in the static directory,
    every member is a candidate co-occurrence: bind their phone to that
    shop. Real signal — they're literally in the merchant's WA group.

    Returns count of bindings added (best-effort)."""
    chat_id = chat.get("chat_id")
    chat_name = chat.get("chat_name")
    members = chat.get("members") or {}
    if not members:
        return 0

    # Resolve which shop_url this chat belongs to. Two routes: (a) we
    # already learned it via the static directory's whatsapp_groups
    # table (group_name match), or (b) the chat_id is a 1:1 chat whose
    # peer phone is in our static directory.
    shop_url = None
    if chat_name:
        wag = (
            db.query(WhatsAppGroup)
            .filter(WhatsAppGroup.group_name == chat_name)
            .filter(WhatsAppGroup.shop_url.isnot(None))
            .first()
        )
        if wag:
            shop_url = wag.shop_url
    if shop_url is None and chat_id and "@c.us" in chat_id:
        # 1:1 chat — chat_id is the contact JID
        cp_digits = chat_id.split("@", 1)[0]
        shop_url = phone_to_shop.get(norm_phone(cp_digits))

    if shop_url is None:
        return 0

    added = 0
    for jid in members.keys():
        if not jid or "@c.us" not in jid:
            continue
        digits = jid.split("@", 1)[0]
        if not digits:
            continue
        try:
            add_binding(
                db,
                "phone", digits,
                "shop_url", shop_url,
                source="periskope_member",
                confidence=0.85,  # high — they're in the merchant's group
                evidence_table="whatsapp_groups",
                evidence_id=chat_id,
            )
            added += 1
        except ValueError:
            pass
    return added


def backfill_chats(headers: dict, since: Optional[str], max_pages: Optional[int], dry_run: bool):
    db = SessionLocal()
    total_chats = 0
    total_member_bindings = 0
    learned_groups = 0
    try:
        phone_to_shop = build_phone_to_shop(db)

        params = {}
        # Periskope's chats endpoint doesn't document a date filter — pull
        # everything. The API returns chats in DESC updated_at order, so
        # active chats come first.

        for page in _paginate("/chats", "chats", params, headers, max_pages):
            for chat in page:
                # Run the live-webhook handler so we get exactly the same
                # behavior end-to-end.
                result = _handle_chat_created(chat, db)
                if result.get("learned_name"):
                    learned_groups += 1
                total_chats += 1

                # Grow the graph from members.
                total_member_bindings += _grow_graph_from_members(db, chat, phone_to_shop)
            db.flush()  # release memory between pages

        if dry_run:
            db.rollback()
            log.info("dry-run — chats rolled back")
        else:
            db.commit()
        log.info("chats: total=%d learned_names=%d member_bindings_added=%d",
                 total_chats, learned_groups, total_member_bindings)
        return total_chats
    finally:
        db.close()


# ── message backfill ──────────────────────────────────────────────────────
# Performance: the live webhook calls _handle_message_created per row,
# which rebuilds the phone_to_shop dict on every resolve. That's fine for
# 1 message at a time but kills throughput at 10k+ rows. The backfill
# uses a bulk path: extract → bulk insert → defer resolution to a single
# post-pass via reprocess_pending.py.
import json as _json
import re as _re

from crm_app.db import insert_on_conflict_do_nothing  # noqa: E402
from crm_app.webhooks.periskope import (  # noqa: E402
    _collapse_type, _digits_from_jid, _is_group_jid, _parse_periskope_ts, _group_name_for,
)


def _msg_to_row(msg: dict, db: Session) -> Optional[dict]:
    """Translate a Periskope REST message into a whatsapp_raw_messages row.
    Returns None when required fields are missing (skipped + logged)."""
    chat_id = msg.get("chat_id")
    sender_phone_raw = msg.get("sender_phone") or msg.get("from")
    sender_phone_digits = _digits_from_jid(sender_phone_raw)
    ts = _parse_periskope_ts(msg.get("timestamp"))

    if not (sender_phone_digits and ts and chat_id):
        return None

    if _is_group_jid(chat_id):
        group_name = _group_name_for(db, chat_id) or chat_id
    else:
        group_name = chat_id

    media = msg.get("media") if isinstance(msg.get("media"), dict) else None
    return {
        "group_name": group_name,
        "sender_phone": sender_phone_digits,
        "sender_name": None,
        "timestamp": ts,
        "body": msg.get("body") or "",
        "is_from_me": bool(msg.get("from_me")),
        "message_type": _collapse_type(msg.get("message_type")),
        "media_url": media.get("path") if media else None,
        "source_message_id": msg.get("message_id"),
        "received_at": datetime.utcnow(),
        "resolution_status": "pending",
    }


def backfill_messages(headers: dict, since: Optional[str], until: Optional[str],
                      max_pages: Optional[int], dry_run: bool, start_offset: int = 0):
    db = SessionLocal()
    total_seen = total_inserted = total_skipped = 0
    try:
        params = {}
        if since:
            params["start_time"] = since
        if until:
            params["end_time"] = until

        # Pre-cache: chat_id → group_name (for JID lookup) so we don't
        # hit DB per message.
        chat_name_cache = {
            jid: name
            for jid, name in db.query(WhatsAppGroup.group_jid, WhatsAppGroup.group_name).all()
            if jid
        }
        log.info("group_name cache: %d entries", len(chat_name_cache))

        for page in _paginate("/chats/messages", "messages", params, headers, max_pages, start_offset):
            rows = []
            for msg in page:
                total_seen += 1
                # Fast-path group_name resolution from in-memory cache
                chat_id = msg.get("chat_id")
                sender_phone_raw = msg.get("sender_phone") or msg.get("from")
                sender_phone_digits = _digits_from_jid(sender_phone_raw)
                ts = _parse_periskope_ts(msg.get("timestamp"))
                if not (sender_phone_digits and ts and chat_id):
                    total_skipped += 1
                    continue

                if _is_group_jid(chat_id):
                    group_name = chat_name_cache.get(chat_id) or chat_id
                else:
                    group_name = chat_id

                media = msg.get("media") if isinstance(msg.get("media"), dict) else None
                rows.append({
                    "group_name": group_name,
                    "sender_phone": sender_phone_digits,
                    "sender_name": None,
                    "timestamp": ts,
                    "body": msg.get("body") or "",
                    "is_from_me": bool(msg.get("from_me")),
                    "message_type": _collapse_type(msg.get("message_type")),
                    "media_url": media.get("path") if media else None,
                    "source_message_id": msg.get("message_id"),
                    "received_at": datetime.utcnow(),
                    "resolution_status": "pending",
                })

            if rows:
                stmt = insert_on_conflict_do_nothing(
                    WhatsAppRawMessage, rows,
                    ["group_name", "sender_phone", "timestamp", "body"],
                    returning=WhatsAppRawMessage.id,
                )
                result = db.execute(stmt)
                total_inserted += len(result.fetchall())

            if not dry_run:
                db.commit()

        if dry_run:
            db.rollback()
            log.info("dry-run — messages rolled back")
        else:
            db.commit()

        log.info(
            "messages: seen=%d inserted=%d skipped=%d (resolution deferred — run scripts/reprocess_pending.py next)",
            total_seen, total_inserted, total_skipped,
        )
        return total_seen
    finally:
        db.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--since", type=str, default=None,
                   help="ISO date for messages start_time filter (e.g. 2025-01-01)")
    p.add_argument("--until", type=str, default=None,
                   help="ISO date for messages end_time filter")
    p.add_argument("--max-pages", type=int, default=None,
                   help="cap pages per endpoint (sanity testing)")
    p.add_argument("--chats-only", action="store_true",
                   help="skip messages backfill")
    p.add_argument("--messages-only", action="store_true",
                   help="skip chats backfill")
    p.add_argument("--dry-run", action="store_true",
                   help="roll back at end of each phase")
    p.add_argument("--start-offset", type=int, default=0,
                   help="resume messages backfill from this offset (used after a crash)")
    args = p.parse_args()

    Base.metadata.create_all(bind=engine)
    headers = _headers()

    if not args.messages_only:
        log.info("=== backfilling chats ===")
        backfill_chats(headers, args.since, args.max_pages, args.dry_run)

    if not args.chats_only:
        log.info("=== backfilling messages ===")
        backfill_messages(headers, args.since, args.until, args.max_pages, args.dry_run,
                          args.start_offset)

    log.info("=== done ===")


if __name__ == "__main__":
    main()
