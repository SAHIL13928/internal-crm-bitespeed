"""Cron-style runner: pulls upcoming Google Calendar events for every
active CalendarConnection, upserts them, resolves to merchants via the
identity graph (using EXTERNAL attendee emails), and grows the graph
with any newly-discovered (email, shop_url) bindings.

Per-connection failures are isolated — one expired refresh token never
kills the run for everyone else. Failing connections get
`status='failing'` + `last_error` so /auth/google/connections surfaces
them for the user.

Idempotent on re-run: events are upserted by (connection_id,
google_event_id). Re-running an unchanged event is a no-op. Re-running
a changed event picks up the latest fields.

Run:
    python scripts/sync_google_calendars.py
    python scripts/sync_google_calendars.py --window-days 30
    python scripts/sync_google_calendars.py --user-email someone@bitespeed.co
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session  # noqa: E402

from crm_app.db import SessionLocal  # noqa: E402
from crm_app.google.client import (  # noqa: E402
    CalendarAuthError,
    get_calendar_service_for,
)
from crm_app.identity import add_binding, resolve_shop_url_for, CONFLICT  # noqa: E402
from crm_app.models import CalendarConnection, CalendarEvent  # noqa: E402
from crm_app.time_utils import utcnow_naive  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sync_google_calendars")

INTERNAL_DOMAINS = {"bitespeed.co"}


def _is_internal(email: Optional[str]) -> bool:
    if not email:
        return True
    return email.lower().split("@")[-1] in INTERNAL_DOMAINS


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    """Google returns either {dateTime: "2026-04-30T10:00:00+05:30"}
    (timed event) or {date: "2026-04-30"} (all-day). Normalize to
    naive UTC."""
    if not s:
        return None
    try:
        if "T" in s:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return d.astimezone(timezone.utc).replace(tzinfo=None) if d.tzinfo else d
        # All-day event — interpret as midnight local. Naive.
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _extract_external_emails(event: dict, organizer_email: Optional[str]) -> list[str]:
    """External (non-bitespeed.co) attendee emails. Skip the organizer
    even if they're external — the organizer is usually the merchant
    OR our own user, neither of which is useful new evidence."""
    out: list[str] = []
    for a in event.get("attendees") or []:
        email = (a.get("email") or "").strip().lower()
        if not email or _is_internal(email):
            continue
        if organizer_email and email == organizer_email.lower():
            continue
        out.append(email)
    return out


def _meeting_link(event: dict) -> Optional[str]:
    """Prefer the Google Meet entryPoint; fall back to hangoutLink."""
    cd = event.get("conferenceData") or {}
    for ep in cd.get("entryPoints") or []:
        if ep.get("entryPointType") == "video" and ep.get("uri"):
            return ep["uri"]
    return event.get("hangoutLink") or None


# ── per-connection sync ──────────────────────────────────────────────────
def sync_one_connection(db: Session, conn: CalendarConnection, window_days: int) -> dict:
    """Returns {fetched, upserted, resolved} stats."""
    log.info("syncing %s (mode=%s)", conn.user_email, conn.auth_mode)

    try:
        service = get_calendar_service_for(conn.user_email, db)
    except CalendarAuthError as e:
        msg = str(e)
        if "revoked" in msg or "invalid_grant" in msg.lower():
            conn.status = "revoked"
        else:
            conn.status = "failing"
        conn.last_error = msg[:500]
        db.commit()
        log.warning("auth failed for %s — %s", conn.user_email, msg)
        return {"fetched": 0, "upserted": 0, "resolved": 0, "error": msg}

    now = datetime.now(timezone.utc)
    time_min = now.isoformat()
    time_max = (now + timedelta(days=window_days)).isoformat()

    fetched = upserted = resolved = 0
    page_token: Optional[str] = None

    try:
        while True:
            resp = (
                service.events()
                .list(
                    calendarId="primary",
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,      # expand recurring → individual instances
                    orderBy="startTime",
                    maxResults=250,
                    pageToken=page_token,
                )
                .execute()
            )
            for ev in resp.get("items", []):
                fetched += 1
                if upsert_event(db, conn, ev):
                    upserted += 1
                # resolve_event_to_shop is the most expensive step, but it
                # also returns a flag we record in the row.
                if resolve_event_to_shop(db, conn, ev):
                    resolved += 1
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        conn.status = "active"
        conn.last_error = None
        conn.last_synced_at = utcnow_naive()
        db.commit()

    except Exception as e:  # noqa: BLE001
        # API errors (network, 5xx, rate limit). One bad connection
        # shouldn't poison the rest of the run.
        msg = f"{type(e).__name__}: {e}"
        log.exception("sync error for %s", conn.user_email)
        conn.status = "failing"
        conn.last_error = msg[:500]
        db.commit()
        return {"fetched": fetched, "upserted": upserted, "resolved": resolved, "error": msg}

    log.info(
        "  → %s: fetched=%d upserted=%d resolved=%d",
        conn.user_email, fetched, upserted, resolved,
    )
    return {"fetched": fetched, "upserted": upserted, "resolved": resolved}


def upsert_event(db: Session, conn: CalendarConnection, ev: dict) -> bool:
    """Insert or update a CalendarEvent row. Returns True if anything
    changed (insert or field diff)."""
    google_id = ev.get("id")
    if not google_id:
        return False

    start_time = _parse_iso((ev.get("start") or {}).get("dateTime") or (ev.get("start") or {}).get("date"))
    if start_time is None:
        return False
    end_time = _parse_iso((ev.get("end") or {}).get("dateTime") or (ev.get("end") or {}).get("date"))

    organizer = (ev.get("organizer") or {}).get("email")
    attendees = ev.get("attendees") or []

    existing = (
        db.query(CalendarEvent)
        .filter_by(connection_id=conn.id, google_event_id=google_id)
        .one_or_none()
    )
    is_new = existing is None
    if is_new:
        existing = CalendarEvent(connection_id=conn.id, google_event_id=google_id)
        db.add(existing)

    existing.summary = ev.get("summary")
    existing.description = ev.get("description")
    existing.start_time = start_time
    existing.end_time = end_time
    existing.meeting_link = _meeting_link(ev)
    existing.attendee_emails = [
        {"email": a.get("email"), "response_status": a.get("responseStatus")}
        for a in attendees if a.get("email")
    ]
    existing.organizer_email = organizer
    existing.raw_payload = ev
    db.flush()
    return True


def resolve_event_to_shop(db: Session, conn: CalendarConnection, ev: dict) -> bool:
    """Use external attendee emails to map the event to a shop_url via
    the identity graph. On success, also call add_binding for every
    new (email, shop_url) co-occurrence so the graph compounds."""
    organizer = (ev.get("organizer") or {}).get("email")
    external = _extract_external_emails(ev, organizer)

    found_shop: Optional[str] = None
    saw_conflict = False
    for email in external:
        result = resolve_shop_url_for(db, "email", email)
        if result == CONFLICT:
            saw_conflict = True
            continue
        if result:
            if found_shop and found_shop != result:
                saw_conflict = True
                break
            found_shop = result

    google_id = ev.get("id")
    row = (
        db.query(CalendarEvent)
        .filter_by(connection_id=conn.id, google_event_id=google_id)
        .one_or_none()
    )
    if row is None:
        return False

    if found_shop and not saw_conflict:
        row.shop_url = found_shop
        row.resolution_status = "resolved"
        # Grow graph: every external attendee co-occurred in this
        # event with the resolved shop. Add bindings.
        for email in external:
            try:
                add_binding(
                    db,
                    "email", email,
                    "shop_url", found_shop,
                    source="google_calendar",
                    confidence=0.9,
                    evidence_table="calendar_events",
                    evidence_id=str(row.id),
                )
            except ValueError:
                pass
        db.flush()
        return True

    if saw_conflict:
        row.resolution_status = "conflict"
    else:
        row.resolution_status = "pending"
    db.flush()
    return False


# ── orchestrator ────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser()
    p.add_argument("--window-days", type=int, default=30,
                   help="how far ahead to fetch events (default 30)")
    p.add_argument("--user-email", type=str, default=None,
                   help="sync only this connection (debugging)")
    args = p.parse_args()

    db: Session = SessionLocal()
    try:
        q = db.query(CalendarConnection).filter(CalendarConnection.status != "revoked")
        if args.user_email:
            q = q.filter(CalendarConnection.user_email == args.user_email)
        conns = q.all()
        log.info("connections to sync: %d", len(conns))

        total = {"fetched": 0, "upserted": 0, "resolved": 0, "errors": 0}
        for conn in conns:
            r = sync_one_connection(db, conn, args.window_days)
            total["fetched"] += r["fetched"]
            total["upserted"] += r["upserted"]
            total["resolved"] += r["resolved"]
            if "error" in r:
                total["errors"] += 1

        log.info("== done == %s", json.dumps(total))
    finally:
        db.close()


if __name__ == "__main__":
    main()
