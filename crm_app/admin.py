"""Admin / diagnostics endpoints — surface coverage gaps so we can keep improving
the merchant-mapping over time. Mostly read-only; the calendar/enable-dwd
endpoint mutates connection rows.

Endpoints that mutate or surface raw merchant data are protected by an
`X-Admin-Secret` header compared against ADMIN_SECRET. The coverage / orphan
endpoints stay unauthenticated for now since they're already used by tooling.
"""
import hmac
import os
from collections import Counter
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .db import get_db
from .identity import find_conflicts
from .models import (
    CalendarConnection, Call, Contact, Meeting, MeetingAttendee, Note, Shop,
    WhatsAppRawMessage,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _require_admin(header_value: Optional[str]):
    secret = os.environ.get("ADMIN_SECRET")
    if not secret:
        raise HTTPException(503, "ADMIN_SECRET not configured on server")
    if not header_value or not hmac.compare_digest(header_value, secret):
        raise HTTPException(401, "invalid admin secret")


@router.get("/coverage")
def coverage(db: Session = Depends(get_db)):
    """One-shot stats — useful as a 'mapping health' dashboard tile."""
    total_meetings = db.query(func.count(Meeting.id)).scalar() or 0
    mapped_meetings = db.query(func.count(Meeting.id)).filter(Meeting.shop_url.isnot(None)).scalar() or 0
    total_calls = db.query(func.count(Call.id)).scalar() or 0
    mapped_calls = db.query(func.count(Call.id)).filter(Call.shop_url.isnot(None)).scalar() or 0
    total_shops = db.query(func.count(Shop.shop_url)).scalar() or 0
    shops_with_brand = db.query(func.count(Shop.shop_url)).filter(Shop.brand_name.isnot(None)).scalar() or 0

    by_source = dict(
        db.query(Meeting.mapping_source, func.count(Meeting.id))
        .group_by(Meeting.mapping_source).all()
    )

    return {
        "meetings": {
            "total": total_meetings,
            "mapped": mapped_meetings,
            "orphan": total_meetings - mapped_meetings,
            "pct": round(100 * mapped_meetings / total_meetings, 1) if total_meetings else 0,
            "by_mapping_source": {k or "none": v for k, v in by_source.items()},
        },
        "calls": {
            "total": total_calls,
            "mapped": mapped_calls,
            "orphan": total_calls - mapped_calls,
            "pct": round(100 * mapped_calls / total_calls, 1) if total_calls else 0,
        },
        "shops": {
            "total": total_shops,
            "with_brand_name": shops_with_brand,
        },
    }


@router.get("/orphans/meetings")
def orphan_meetings(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Orphan meetings with their external attendee emails — the raw material
    for targeted mapping fixes."""
    rows = (
        db.query(Meeting)
        .filter(Meeting.shop_url.is_(None))
        .order_by(Meeting.date.desc().nullslast())
        .limit(limit).offset(offset).all()
    )
    out = []
    for m in rows:
        ext_emails = [
            a.email for a in (m.attendees or [])
            if a.email and not a.is_internal
        ]
        out.append({
            "id": m.id,
            "title": m.title,
            "date": m.date.isoformat() if m.date else None,
            "meeting_link": m.meeting_link,
            "external_attendees": ext_emails,
            "summary_short": (m.summary_short or "")[:200],
        })
    total = db.query(func.count(Meeting.id)).filter(Meeting.shop_url.is_(None)).scalar()
    return {"total": total, "limit": limit, "offset": offset, "items": out}


@router.get("/orphans/calls")
def orphan_calls(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Calls with no shop binding — usually a phone format mismatch or a number
    we don't have in contacts."""
    rows = (
        db.query(Call)
        .filter(Call.shop_url.is_(None))
        .order_by(Call.started_at.desc().nullslast())
        .limit(limit).offset(offset).all()
    )
    out = [{
        "id": c.id,
        "started_at": c.started_at.isoformat() if c.started_at else None,
        "direction": c.direction,
        "from_number": c.from_number,
        "to_number": c.to_number,
        "agent_email": c.agent_email,
        "duration_sec": c.duration_sec,
        "connected": c.connected,
    } for c in rows]
    total = db.query(func.count(Call.id)).filter(Call.shop_url.is_(None)).scalar()
    return {"total": total, "limit": limit, "offset": offset, "items": out}


@router.get("/orphans/emails")
def orphan_emails(
    top: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """External attendee emails that show up in orphan meetings, grouped by
    domain — sorted by frequency. Highest-frequency domains are the best
    candidates for next-round mapping work (one entry could rescue many
    meetings at once)."""
    rows = (
        db.query(MeetingAttendee.email)
        .join(Meeting, MeetingAttendee.meeting_id == Meeting.id)
        .filter(Meeting.shop_url.is_(None))
        .filter(MeetingAttendee.email.isnot(None))
        .filter(MeetingAttendee.is_internal.is_(False))
        .all()
    )
    by_domain: Counter = Counter()
    by_email: Counter = Counter()
    for (email,) in rows:
        e = (email or "").strip().lower()
        if not e or "@" not in e:
            continue
        by_email[e] += 1
        by_domain[e.split("@", 1)[-1]] += 1

    return {
        "unique_emails": len(by_email),
        "unique_domains": len(by_domain),
        "top_domains": [
            {"domain": d, "orphan_meeting_count": n}
            for d, n in by_domain.most_common(top)
        ],
        "top_emails": [
            {"email": e, "orphan_meeting_count": n}
            for e, n in by_email.most_common(top)
        ],
    }


@router.get("/orphans/numbers")
def orphan_numbers(
    top: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Counterparty phone numbers from orphan calls, grouped by frequency.
    A number that appears in many calls but no contacts is a likely-merchant
    we're missing."""
    rows = (
        db.query(Call.direction, Call.from_number, Call.to_number)
        .filter(Call.shop_url.is_(None))
        .all()
    )
    counter: Counter = Counter()
    for direction, frm, to in rows:
        # Counterparty is the side that isn't ours
        cp = to if (direction or "").startswith("out") else frm
        if cp:
            counter[cp] += 1

    return {
        "unique_numbers": len(counter),
        "top_numbers": [
            {"number": n, "orphan_call_count": c}
            for n, c in counter.most_common(top)
        ],
    }


@router.get("/conflicts")
def list_conflicts(
    limit: int = Query(100, ge=1, le=1000),
    x_admin_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Identity-graph and per-message conflicts — components where the BFS
    reaches >1 distinct shop_url, plus raw messages whose resolver flagged
    a static-directory conflict.

    Header-protected: send `X-Admin-Secret: <ADMIN_SECRET>`.
    """
    _require_admin(x_admin_secret)
    graph_conflicts = find_conflicts(db, limit=limit)

    raw_msg_conflicts = (
        db.query(WhatsAppRawMessage)
        .filter(WhatsAppRawMessage.resolution_status == "conflict")
        .order_by(WhatsAppRawMessage.id.desc())
        .limit(limit)
        .all()
    )
    raw_items = [
        {
            "id": r.id,
            "group_name": r.group_name,
            "sender_phone": r.sender_phone,
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            "resolution_method": r.resolution_method,
        }
        for r in raw_msg_conflicts
    ]
    return {
        "identity_graph_conflicts": graph_conflicts,
        "whatsapp_message_conflicts": raw_items,
    }


@router.post("/calendar/enable-dwd")
def enable_dwd(
    payload: dict,
    x_admin_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Flip selected calendar_connections from per-user OAuth to
    service-account / domain-wide-delegation mode.

    Body:
      { "user_emails": ["a@bitespeed.co", "b@bitespeed.co"] }
      OR
      { "user_emails": "all" }   (every active connection in the DB)

    Validates that GOOGLE_SERVICE_ACCOUNT_JSON is configured before
    flipping. After this returns, the next sync run uses the SA's
    credentials with `.with_subject(user_email)` instead of the user's
    refresh token. Refresh tokens stay encrypted in the DB but are
    no longer used.
    """
    _require_admin(x_admin_secret)

    if not os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"):
        raise HTTPException(503, "GOOGLE_SERVICE_ACCOUNT_JSON not configured — set it before flipping mode")

    selector = (payload or {}).get("user_emails")
    if selector == "all":
        rows = db.query(CalendarConnection).filter(CalendarConnection.status != "revoked").all()
    elif isinstance(selector, list) and all(isinstance(e, str) for e in selector):
        rows = (
            db.query(CalendarConnection)
            .filter(CalendarConnection.user_email.in_(selector))
            .filter(CalendarConnection.status != "revoked")
            .all()
        )
    else:
        raise HTTPException(400, "user_emails must be a list of emails or the literal string 'all'")

    flipped = 0
    for conn in rows:
        if conn.auth_mode != "dwd_impersonation":
            conn.auth_mode = "dwd_impersonation"
            conn.last_error = None
            conn.status = "active"  # re-arm even if previously failing
            flipped += 1
    db.commit()
    return {
        "flipped": flipped,
        "total_matched": len(rows),
        "user_emails": [r.user_email for r in rows],
    }


@router.get("/orphans/shops")
def orphan_shops(
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """Shops that have zero meetings AND zero calls — the master CSV listed
    them but we have no actual activity. Possibly stale or never-engaged."""
    # subqueries: shops appearing in meetings/calls
    subq_m = db.query(Meeting.shop_url).filter(Meeting.shop_url.isnot(None)).distinct()
    subq_c = db.query(Call.shop_url).filter(Call.shop_url.isnot(None)).distinct()
    rows = (
        db.query(Shop.shop_url, Shop.brand_name)
        .filter(~Shop.shop_url.in_(subq_m))
        .filter(~Shop.shop_url.in_(subq_c))
        .order_by(Shop.shop_url)
        .limit(limit).all()
    )
    total = (
        db.query(func.count(Shop.shop_url))
        .filter(~Shop.shop_url.in_(subq_m))
        .filter(~Shop.shop_url.in_(subq_c)).scalar()
    )
    return {
        "total": total,
        "limit": limit,
        "items": [{"shop_url": u, "brand_name": b} for u, b in rows],
    }
