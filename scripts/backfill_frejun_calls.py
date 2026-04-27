"""One-time backfill of historical FreJun calls.

Webhooks already handle new calls. This script pulls the existing history via
FreJun's REST API and runs each record through the SAME mapper the webhook
receiver uses (`etl.load_frejun.apply_call_record`), so behavior cannot diverge.

Endpoint:    GET https://api.frejun.com/integrations/calls/
Auth:        Authorization: Api-Key <FREJUN_API_KEY>
Pagination:  follow `next` URL in each response (DRF-style)

Idempotent: re-running upserts by FreJun's call uuid/id; no duplicates.
Rate limits: respects Retry-After on 429, else exponential backoff.

Usage:
    python scripts/backfill_frejun_calls.py --dry-run --limit 5
    python scripts/backfill_frejun_calls.py --dry-run
    python scripts/backfill_frejun_calls.py --since 2026-04-01
    python scripts/backfill_frejun_calls.py
"""
import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from typing import Iterator, Optional

import requests
from dotenv import load_dotenv

# Make repo root importable when run as `python scripts/...`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm_app.db import Base, SessionLocal, engine  # noqa: E402
from crm_app.utils import build_phone_to_shop  # noqa: E402
from etl.load_frejun import apply_call_record  # noqa: E402

load_dotenv()

ENDPOINT = "https://api.frejun.com/api/v2/integrations/calls/"
MAX_429_RETRIES = 6
HTTP_TIMEOUT = 120  # FreJun can be slow on first page when history is large
DEFAULT_PAGE_SIZE = 50  # keeps each page small; FreJun ignores it if unsupported

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_frejun")

# Sanitization patterns for the one-time sample print
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"\+?\d{10,15}")


def _sanitize(obj):
    """Deep copy with phones/emails replaced by fakes — for safe sample printing."""
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(x) for x in obj]
    if isinstance(obj, str):
        s = _EMAIL_RE.sub("user@example.com", obj)
        s = _PHONE_RE.sub("+91XXXXXXXXXX", s)
        return s
    return obj


def _to_frejun_date(yyyy_mm_dd: str) -> str:
    """YYYY-MM-DD -> DD/MM/YY HH:MM:SS (per FreJun docs)."""
    d = datetime.strptime(yyyy_mm_dd, "%Y-%m-%d")
    return d.strftime("%d/%m/%y %H:%M:%S")


def _parse_since(since: Optional[str]) -> Optional[datetime]:
    if not since:
        return None
    return datetime.strptime(since, "%Y-%m-%d")


def _record_matches_since(record: dict, since_dt: Optional[datetime]) -> bool:
    """Client-side defence in depth — keeps only records >= --since regardless of
    whether the server honored the `date` query param."""
    if since_dt is None:
        return True
    raw = record.get("start_time") or record.get("started_at")
    if not raw:
        return True  # unknowns aren't dropped
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, AttributeError):
        return True
    return ts >= since_dt


def fetch_pages(api_key: str, since: Optional[str]) -> Iterator[list]:
    """Yield each page's records. Handles pagination + 429 backoff."""
    headers = {"Authorization": f"Api-Key {api_key}"}
    initial_params = {"page_size": DEFAULT_PAGE_SIZE}
    if since:
        initial_params["date"] = _to_frejun_date(since)

    url = ENDPOINT
    page_num = 0
    while url:
        page_num += 1
        # `next` URLs from DRF pagination already include all query params,
        # so only attach params on the first request.
        params = initial_params if page_num == 1 else None

        for attempt in range(MAX_429_RETRIES):
            resp = requests.get(url, headers=headers, params=params, timeout=HTTP_TIMEOUT)
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                wait = int(retry_after) if retry_after and retry_after.isdigit() else (2 ** attempt)
                logger.warning("429 received; sleeping %ds (attempt %d/%d)",
                               wait, attempt + 1, MAX_429_RETRIES)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            break
        else:
            raise RuntimeError(f"exceeded {MAX_429_RETRIES} retries on 429 at {url}")

        body = resp.json()
        # FreJun v2 shape: {success, data: {count, next, previous, results: [...]}}
        # Falls back to DRF-default {results, next} or a bare list for resilience.
        if isinstance(body, list):
            results, next_url = body, None
        elif isinstance(body, dict):
            data = body.get("data")
            if isinstance(data, dict) and "results" in data:
                results = data.get("results") or []
                next_url = data.get("next")
            else:
                results = body.get("results") or body.get("calls") or []
                next_url = body.get("next")
        else:
            results, next_url = [], None

        logger.info("Page %d: fetched %d calls", page_num, len(results))
        yield results
        url = next_url


def main():
    parser = argparse.ArgumentParser(description="Backfill historical FreJun calls.")
    parser.add_argument("--dry-run", action="store_true",
                        help="fetch + map but roll back; no rows persisted")
    parser.add_argument("--limit", type=int, default=None,
                        help="stop after N records have been processed")
    parser.add_argument("--since", type=str, default=None,
                        help="YYYY-MM-DD inclusive lower bound on call start_time")
    args = parser.parse_args()

    api_key = os.environ.get("FREJUN_API_KEY")
    if not api_key:
        logger.error("FREJUN_API_KEY not set. Add it to .env and re-run.")
        sys.exit(2)

    since_dt = _parse_since(args.since)

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        phone_to_shop = build_phone_to_shop(db)
        logger.info("phone_to_shop map: %d entries", len(phone_to_shop))

        total_fetched = 0
        total_inserted = 0
        total_updated = 0
        total_skipped = 0
        total_errors = 0
        running_total = 0
        printed_sample = False
        limit_hit = False

        for results in fetch_pages(api_key, args.since):
            running_total += len(results)
            logger.info("Page running total: %d records seen from server", running_total)

            for r in results:
                if not _record_matches_since(r, since_dt):
                    total_skipped += 1
                    continue

                if not printed_sample:
                    logger.info(
                        "Sample call (sanitized — phones/emails fake):\n%s",
                        json.dumps(_sanitize(r), indent=2, ensure_ascii=False),
                    )
                    printed_sample = True

                total_fetched += 1
                cid = r.get("uuid") or r.get("id") or "<no-id>"
                try:
                    with db.begin_nested():
                        _, is_new, matched = apply_call_record(r, db, phone_to_shop)
                    if is_new:
                        total_inserted += 1
                        logger.info("ok    %s  (new, matched_shop=%s)", cid, matched)
                    else:
                        total_updated += 1
                        logger.info("ok    %s  (updated, matched_shop=%s)", cid, matched)
                except ValueError as e:
                    total_skipped += 1
                    logger.warning("skip  %s  %s", cid, e)
                except Exception as e:
                    total_errors += 1
                    logger.exception("err   %s  %s", cid, e)

                if args.limit is not None and total_fetched >= args.limit:
                    logger.info("Hit --limit=%d; stopping fetch", args.limit)
                    limit_hit = True
                    break

            if limit_hit:
                break

        if args.dry_run:
            db.rollback()
            logger.info("DRY RUN: rolled back; no rows persisted to crm.db")
        else:
            db.commit()
            logger.info("Committed.")

        logger.info("---- summary ----")
        logger.info("fetched (mapped):   %d", total_fetched)
        verb = "would-insert" if args.dry_run else "inserted"
        logger.info("%-19s %d", verb + ":", total_inserted)
        verb = "would-update" if args.dry_run else "updated"
        logger.info("%-19s %d", verb + ":", total_updated)
        logger.info("skipped:            %d  (no id / before --since)", total_skipped)
        logger.info("errors:             %d", total_errors)
    finally:
        db.close()


if __name__ == "__main__":
    main()
