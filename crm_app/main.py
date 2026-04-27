"""FastAPI backend for the internal CS-CRM."""
import json
import logging
import os
from datetime import datetime, timedelta
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import desc, func, or_
from sqlalchemy.orm import Session, selectinload

from .db import Base, engine, get_db
from .models import (
    Binding,
    Call,
    Contact,
    Identity,
    Issue,
    Meeting,
    MeetingAttendee,
    Note,
    Shop,
    WhatsAppGroup,
    WhatsAppGroupEvent,
    WhatsAppMessage,
    WhatsAppRawMessage,
)
from .schemas import (
    AttendeeOut,
    CallDetail,
    CallListItem,
    ContactOut,
    DNCRequest,
    IssueCreate,
    IssueOut,
    IssuePatch,
    MeetingDetail,
    MeetingListItem,
    NoteCreate,
    NoteOut,
    ShopKpi,
    ShopProfile,
    ShopSummary,
    TimelineItem,
    WhatsAppGroupOut,
)
from .admin import router as admin_router
from .webhooks import frejun_router, whatsapp_router

logger = logging.getLogger("crm")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)


def _run_migrations():
    """Idempotent ALTERs. Replace with Alembic when scale demands."""
    with engine.begin() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(whatsapp_groups)").fetchall()}
        if cols and "group_jid" not in cols:
            conn.exec_driver_sql("ALTER TABLE whatsapp_groups ADD COLUMN group_jid VARCHAR")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_whatsapp_groups_group_jid ON whatsapp_groups(group_jid)")
            logger.info("migration: added whatsapp_groups.group_jid + index")


# Ensure tables exist on app start (no-op if already created by ETL)
Base.metadata.create_all(bind=engine)
_run_migrations()

app = FastAPI(title="CS-CRM API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(whatsapp_router)
app.include_router(frejun_router)
app.include_router(admin_router)


# Spec-mandated top-level alias: /admin/conflicts. Delegates to the same
# handler that lives at /api/admin/conflicts so behavior cannot diverge.
from .admin import list_conflicts as _list_conflicts  # noqa: E402


@app.get("/admin/conflicts", tags=["admin"])
def admin_conflicts_alias(
    limit: int = Query(100, ge=1, le=1000),
    x_admin_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    return _list_conflicts(limit=limit, x_admin_secret=x_admin_secret, db=db)

# Static frontend (single-page app) at /app/. Keeps GET / clean for Render's health probe.
_frontend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.isdir(_frontend_dir):
    app.mount("/app", StaticFiles(directory=_frontend_dir, html=True), name="frontend")


@app.get("/")
def root():
    """Lightweight liveness probe — used by Render's default health check."""
    return {"status": "ok"}


# ── helpers ───────────────────────────────────────────────────────────────
import re as _re

_PHONE_DIGITS = _re.compile(r"\D+")


def _norm_phone_local(p):
    if not p:
        return None
    d = _PHONE_DIGITS.sub("", p)
    return d or None


def _initials(name):
    if not name:
        return None
    parts = [p for p in name.replace(".", " ").split() if p]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _build_shop_phone_to_contact(db: Session, shop_url: str) -> dict:
    """Map digits-only phone -> Contact for one shop's contacts. Used to resolve
    the merchant-side counterparty name on calls."""
    rows = (
        db.query(Contact)
        .filter(Contact.shop_url == shop_url, Contact.phone.isnot(None), Contact.is_internal.is_(False))
        .all()
    )
    out = {}
    for c in rows:
        n = _norm_phone_local(c.phone)
        if n and n not in out:
            out[n] = c
    return out


def _parse_insights(raw):
    """Try to deserialize FreJun's ai_insights JSON. Returns (structured_obj_or_None, narrative_str_or_None)."""
    if not raw:
        return None, None
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        return None, raw  # not JSON — treat as plain narrative
    if not isinstance(obj, dict):
        return obj, None
    summary_block = obj.get("summary") if isinstance(obj.get("summary"), dict) else None
    narrative = (summary_block or {}).get("transcript_summary") if summary_block else None
    return obj, narrative


def _enrich_call_item(c: Call, phone_to_contact: dict) -> CallListItem:
    counterparty = c.to_number if (c.direction or "").startswith("out") else c.from_number
    contact = phone_to_contact.get(_norm_phone_local(counterparty)) if counterparty else None
    insights, narrative = _parse_insights(c.summary)
    # Sentiment: prefer FreJun's ai_insights.sentiment_score.sentiment, else stored field
    sent = None
    if isinstance(insights, dict):
        ss = insights.get("sentiment_score")
        if isinstance(ss, dict):
            sent = ss.get("sentiment")
    sent = sent or c.sentiment
    return CallListItem(
        id=c.id,
        started_at=c.started_at,
        direction=c.direction,
        connected=bool(c.connected),
        duration_sec=c.duration_sec,
        from_number=c.from_number,
        to_number=c.to_number,
        agent_name=c.agent_name,
        counterparty_phone=counterparty,
        counterparty_name=(contact.name if contact else None),
        counterparty_role=(contact.role if contact else None),
        summary=c.summary,
        summary_text=narrative,
        ai_insights=insights,
        sentiment=sent,
        shop_url=c.shop_url,
    )


def _get_shop_or_404(db: Session, shop_url: str) -> Shop:
    shop = db.get(Shop, shop_url.lower())
    if not shop:
        raise HTTPException(status_code=404, detail=f"shop not found: {shop_url}")
    return shop


def _meeting_to_detail(m: Meeting) -> MeetingDetail:
    return MeetingDetail(
        id=m.id,
        title=m.title or "",
        date=m.date,
        duration_min=m.duration_min,
        summary_short=m.summary_short,
        shop_url=m.shop_url,
        mapping_source=m.mapping_source,
        organizer_email=m.organizer_email,
        host_email=m.host_email,
        meeting_link=m.meeting_link,
        transcript_url=m.transcript_url,
        audio_url=m.audio_url,
        video_url=m.video_url,
        summary_overview=m.summary_overview,
        summary_bullet_gist=m.summary_bullet_gist,
        summary_keywords=json.loads(m.summary_keywords) if m.summary_keywords else None,
        action_items=m.action_items,
        attendees=[
            AttendeeOut(email=a.email, display_name=a.display_name, is_internal=a.is_internal)
            for a in (m.attendees or [])
        ],
    )


def _compute_kpi(db: Session, shop_url: str) -> ShopKpi:
    now = datetime.utcnow()
    week_ago = now - timedelta(days=7)
    week_ahead = now + timedelta(days=7)

    upcoming = (
        db.query(func.count(Meeting.id))
        .filter(Meeting.shop_url == shop_url, Meeting.date.between(now, week_ahead))
        .scalar() or 0
    )
    meetings_7d = (
        db.query(func.count(Meeting.id))
        .filter(Meeting.shop_url == shop_url, Meeting.date.between(week_ago, now))
        .scalar() or 0
    )
    calls_7d_attempted = (
        db.query(func.count(Call.id))
        .filter(Call.shop_url == shop_url, Call.started_at.between(week_ago, now))
        .scalar() or 0
    )
    calls_7d_connected = (
        db.query(func.count(Call.id))
        .filter(
            Call.shop_url == shop_url,
            Call.started_at.between(week_ago, now),
            Call.connected.is_(True),
        )
        .scalar() or 0
    )
    open_issues = (
        db.query(func.count(Issue.id))
        .filter(Issue.shop_url == shop_url, Issue.status != "resolved")
        .scalar() or 0
    )
    wa = (
        db.query(func.count(WhatsAppGroup.id))
        .filter(WhatsAppGroup.shop_url == shop_url)
        .scalar() or 0
    )
    meetings_7d_total_minutes = (
        db.query(func.coalesce(func.sum(Meeting.duration_min), 0.0))
        .filter(Meeting.shop_url == shop_url, Meeting.date.between(week_ago, now))
        .scalar()
    )
    last_meeting_at = (
        db.query(func.max(Meeting.date))
        .filter(Meeting.shop_url == shop_url, Meeting.date.isnot(None)).scalar()
    )
    last_call_at = (
        db.query(func.max(Call.started_at))
        .filter(Call.shop_url == shop_url, Call.started_at.isnot(None)).scalar()
    )
    if last_meeting_at and (not last_call_at or last_meeting_at >= last_call_at):
        last_contact_at, last_contact_kind = last_meeting_at, "meeting"
    elif last_call_at:
        last_contact_at, last_contact_kind = last_call_at, "call"
    else:
        last_contact_at, last_contact_kind = None, None

    rate = round(100 * calls_7d_connected / calls_7d_attempted, 1) if calls_7d_attempted else None

    return ShopKpi(
        upcoming_meetings=upcoming,
        meetings_7d=meetings_7d,
        meetings_7d_total_minutes=float(meetings_7d_total_minutes or 0.0) or None,
        calls_7d_attempted=calls_7d_attempted,
        calls_7d_connected=calls_7d_connected,
        calls_7d_connect_rate_pct=rate,
        open_issues=open_issues,
        whatsapp_groups=wa,
        last_contact_at=last_contact_at,
        last_contact_kind=last_contact_kind,
    )


# ── health / meta ─────────────────────────────────────────────────────────
@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    last_msg_ts = db.query(func.max(WhatsAppMessage.received_at)).scalar()
    last_evt_ts = db.query(func.max(WhatsAppGroupEvent.received_at)).scalar()
    return {
        "status": "ok",
        "shops": db.query(func.count(Shop.shop_url)).scalar() or 0,
        "meetings": db.query(func.count(Meeting.id)).scalar() or 0,
        "calls": db.query(func.count(Call.id)).scalar() or 0,
        "calls_with_shop": db.query(func.count(Call.id))
            .filter(Call.shop_url.isnot(None)).scalar() or 0,
        "issues": db.query(func.count(Issue.id)).scalar() or 0,
        "notes": db.query(func.count(Note.id)).scalar() or 0,
        "whatsapp": {
            "messages": db.query(func.count(WhatsAppMessage.message_id)).scalar() or 0,
            "messages_with_shop": db.query(func.count(WhatsAppMessage.message_id))
                .filter(WhatsAppMessage.shop_url.isnot(None)).scalar() or 0,
            "raw_messages": db.query(func.count(WhatsAppRawMessage.id)).scalar() or 0,
            "raw_messages_resolved": db.query(func.count(WhatsAppRawMessage.id))
                .filter(WhatsAppRawMessage.resolution_status == "resolved").scalar() or 0,
            "raw_messages_pending": db.query(func.count(WhatsAppRawMessage.id))
                .filter(WhatsAppRawMessage.resolution_status == "pending").scalar() or 0,
            "raw_messages_conflict": db.query(func.count(WhatsAppRawMessage.id))
                .filter(WhatsAppRawMessage.resolution_status == "conflict").scalar() or 0,
            "groups_known": db.query(func.count(WhatsAppGroup.id))
                .filter(WhatsAppGroup.group_jid.isnot(None)).scalar() or 0,
            "group_events": db.query(func.count(WhatsAppGroupEvent.id)).scalar() or 0,
            "last_message_received_at": last_msg_ts.isoformat() if last_msg_ts else None,
            "last_group_event_received_at": last_evt_ts.isoformat() if last_evt_ts else None,
            "webhook_secret_configured": bool(os.environ.get("WHATSAPP_WEBHOOK_SECRET")),
        },
        "identity_graph": {
            "identities": db.query(func.count(Identity.id)).scalar() or 0,
            "bindings": db.query(func.count(Binding.id)).scalar() or 0,
        },
        "frejun": {
            "webhook_secret_configured": bool(os.environ.get("FREJUN_WEBHOOK_SECRET")),
            "api_key_configured": bool(os.environ.get("FREJUN_API_KEY")),
        },
    }


# ── merchants / shops ─────────────────────────────────────────────────────
@app.get("/api/merchants", response_model=List[ShopSummary])
def list_merchants(
    q: Optional[str] = Query(None, description="search shopUrl or brand_name"),
    health: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    query = db.query(Shop)
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(or_(
            Shop.shop_url.ilike(like),
            Shop.brand_name.ilike(like),
        ))
    if health:
        query = query.filter(Shop.health_status == health)
    rows = query.order_by(Shop.shop_url).offset(offset).limit(limit).all()
    return rows


@app.get("/api/merchants/{shop_url}", response_model=ShopProfile)
def get_merchant(shop_url: str, db: Session = Depends(get_db)):
    shop = (
        db.query(Shop)
        .options(
            selectinload(Shop.contacts),
            selectinload(Shop.whatsapp_groups),
        )
        .filter(Shop.shop_url == shop_url.lower())
        .first()
    )
    if not shop:
        raise HTTPException(status_code=404, detail=f"shop not found: {shop_url}")
    profile = ShopProfile.model_validate(shop)
    profile.kpi = _compute_kpi(db, shop.shop_url)
    return profile


@app.get("/api/merchants/{shop_url}/contacts", response_model=List[ContactOut])
def list_contacts(shop_url: str, db: Session = Depends(get_db)):
    _get_shop_or_404(db, shop_url)
    return (
        db.query(Contact)
        .filter(Contact.shop_url == shop_url.lower())
        .order_by(Contact.is_internal, Contact.id)
        .all()
    )


@app.get("/api/merchants/{shop_url}/whatsapp", response_model=List[WhatsAppGroupOut])
def list_whatsapp_groups(shop_url: str, db: Session = Depends(get_db)):
    _get_shop_or_404(db, shop_url)
    return (
        db.query(WhatsAppGroup)
        .filter(WhatsAppGroup.shop_url == shop_url.lower())
        .order_by(WhatsAppGroup.id)
        .all()
    )


@app.post("/api/merchants/{shop_url}/dnc", response_model=ShopSummary)
def mark_dnc(shop_url: str, body: DNCRequest, db: Session = Depends(get_db)):
    shop = _get_shop_or_404(db, shop_url)
    shop.outreach_status = "dnc"
    shop.health_status = "dnc"
    shop.dnc_reason = body.reason
    shop.dnc_note = body.note
    shop.dnc_revisit_on = body.revisit_on
    db.commit()
    db.refresh(shop)
    return shop


# ── meetings ──────────────────────────────────────────────────────────────
@app.get("/api/merchants/{shop_url}/meetings", response_model=List[MeetingListItem])
def list_meetings(
    shop_url: str,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    _get_shop_or_404(db, shop_url)
    q = (
        db.query(Meeting)
        .options(selectinload(Meeting.attendees))
        .filter(Meeting.shop_url == shop_url.lower())
    )
    if since:
        q = q.filter(Meeting.date >= since)
    if until:
        q = q.filter(Meeting.date <= until)
    rows = q.order_by(desc(Meeting.date)).offset(offset).limit(limit).all()
    out = []
    for m in rows:
        atts = m.attendees or []
        initials = [
            _initials(a.display_name or (a.email.split("@")[0] if a.email else None))
            for a in atts[:3]
        ]
        out.append(MeetingListItem(
            id=m.id,
            title=m.title or "(meeting)",
            date=m.date,
            duration_min=m.duration_min,
            summary_short=m.summary_short,
            shop_url=m.shop_url,
            mapping_source=m.mapping_source,
            attendee_count=len(atts),
            attendee_initials=[i for i in initials if i],
        ))
    return out


@app.get("/api/meetings/{meeting_id}", response_model=MeetingDetail)
def get_meeting(meeting_id: str, db: Session = Depends(get_db)):
    m = (
        db.query(Meeting)
        .options(selectinload(Meeting.attendees))
        .filter(Meeting.id == meeting_id)
        .first()
    )
    if not m:
        raise HTTPException(status_code=404, detail="meeting not found")
    return _meeting_to_detail(m)


# ── calls ─────────────────────────────────────────────────────────────────
@app.get("/api/merchants/{shop_url}/calls", response_model=List[CallListItem])
def list_calls(
    shop_url: str,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    direction: Optional[str] = Query(None, pattern="^(inbound|outbound)$"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    _get_shop_or_404(db, shop_url)
    shop_url = shop_url.lower()
    phone_to_contact = _build_shop_phone_to_contact(db, shop_url)
    q = db.query(Call).filter(Call.shop_url == shop_url)
    if since:
        q = q.filter(Call.started_at >= since)
    if until:
        q = q.filter(Call.started_at <= until)
    if direction:
        q = q.filter(Call.direction == direction)
    rows = q.order_by(desc(Call.started_at)).offset(offset).limit(limit).all()
    return [_enrich_call_item(c, phone_to_contact) for c in rows]


@app.get("/api/calls/{call_id}", response_model=CallDetail)
def get_call(call_id: str, db: Session = Depends(get_db)):
    c = db.get(Call, call_id)
    if not c:
        raise HTTPException(status_code=404, detail="call not found")
    phone_to_contact = (
        _build_shop_phone_to_contact(db, c.shop_url) if c.shop_url else {}
    )
    counterparty = c.to_number if (c.direction or "").startswith("out") else c.from_number
    contact = phone_to_contact.get(_norm_phone_local(counterparty)) if counterparty else None
    insights, narrative = _parse_insights(c.summary)
    sent = None
    if isinstance(insights, dict):
        ss = insights.get("sentiment_score")
        if isinstance(ss, dict):
            sent = ss.get("sentiment")
    sent = sent or c.sentiment
    transcript_segments = None
    if c.transcript:
        try:
            parsed = json.loads(c.transcript)
            if isinstance(parsed, list):
                transcript_segments = parsed
        except (ValueError, TypeError):
            pass
    return CallDetail(
        id=c.id,
        started_at=c.started_at,
        direction=c.direction,
        connected=bool(c.connected),
        duration_sec=c.duration_sec,
        from_number=c.from_number,
        to_number=c.to_number,
        agent_name=c.agent_name,
        counterparty_phone=counterparty,
        counterparty_name=(contact.name if contact else None),
        counterparty_role=(contact.role if contact else None),
        summary=c.summary,
        summary_text=narrative,
        ai_insights=insights,
        sentiment=sent,
        shop_url=c.shop_url,
        agent_email=c.agent_email,
        recording_url=c.recording_url,
        transcript=c.transcript,
        transcript_segments=transcript_segments,
    )


# ── issues ────────────────────────────────────────────────────────────────
@app.get("/api/merchants/{shop_url}/issues", response_model=List[IssueOut])
def list_issues(
    shop_url: str,
    status: Optional[str] = Query(None, pattern="^(open|in_progress|resolved)$"),
    db: Session = Depends(get_db),
):
    _get_shop_or_404(db, shop_url)
    q = db.query(Issue).filter(Issue.shop_url == shop_url.lower())
    if status:
        q = q.filter(Issue.status == status)
    return q.order_by(desc(Issue.opened_at)).all()


@app.post("/api/merchants/{shop_url}/issues", response_model=IssueOut, status_code=201)
def create_issue(shop_url: str, body: IssueCreate, db: Session = Depends(get_db)):
    _get_shop_or_404(db, shop_url)
    issue = Issue(
        shop_url=shop_url.lower(),
        title=body.title,
        description=body.description,
        priority=body.priority,
        status="open",
        source=body.source,
        source_ref=body.source_ref,
        owner=body.owner,
        jira_ticket_id=body.jira_ticket_id,
    )
    db.add(issue)
    db.commit()
    db.refresh(issue)
    return issue


@app.patch("/api/issues/{issue_id}", response_model=IssueOut)
def patch_issue(issue_id: int, body: IssuePatch, db: Session = Depends(get_db)):
    issue = db.get(Issue, issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="issue not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(issue, field, value)
    if body.status == "resolved" and issue.resolved_at is None:
        issue.resolved_at = datetime.utcnow()
    db.commit()
    db.refresh(issue)
    return issue


# ── notes ─────────────────────────────────────────────────────────────────
@app.get("/api/merchants/{shop_url}/notes", response_model=List[NoteOut])
def list_notes(shop_url: str, db: Session = Depends(get_db)):
    _get_shop_or_404(db, shop_url)
    return (
        db.query(Note)
        .filter(Note.shop_url == shop_url.lower())
        .order_by(desc(Note.created_at))
        .all()
    )


@app.post("/api/merchants/{shop_url}/notes", response_model=NoteOut, status_code=201)
def create_note(shop_url: str, body: NoteCreate, db: Session = Depends(get_db)):
    _get_shop_or_404(db, shop_url)
    note = Note(
        shop_url=shop_url.lower(),
        author=body.author,
        body=body.body,
        is_followup=body.is_followup,
        due_at=body.due_at,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


# ── timeline (unified communications view) ────────────────────────────────
@app.get("/api/merchants/{shop_url}/timeline", response_model=List[TimelineItem])
def get_timeline(
    shop_url: str,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = Query(200, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    _get_shop_or_404(db, shop_url)
    shop_url = shop_url.lower()
    items: List[TimelineItem] = []

    mq = db.query(Meeting).filter(Meeting.shop_url == shop_url, Meeting.date.isnot(None))
    if since:
        mq = mq.filter(Meeting.date >= since)
    if until:
        mq = mq.filter(Meeting.date <= until)
    for m in mq.all():
        items.append(TimelineItem(
            type="meeting",
            id=m.id,
            timestamp=m.date,
            title=m.title or "(meeting)",
            summary=m.summary_short,
            metadata={"duration_min": m.duration_min},
        ))

    phone_to_contact = _build_shop_phone_to_contact(db, shop_url)
    cq = db.query(Call).filter(Call.shop_url == shop_url, Call.started_at.isnot(None))
    if since:
        cq = cq.filter(Call.started_at >= since)
    if until:
        cq = cq.filter(Call.started_at <= until)
    for c in cq.all():
        cp = c.to_number if (c.direction or "").startswith("out") else c.from_number
        contact = phone_to_contact.get(_norm_phone_local(cp)) if cp else None
        cp_label = (contact.name if contact else None) or cp or "?"
        title = f"{c.direction or 'call'} · {cp_label}"
        items.append(TimelineItem(
            type="call",
            id=c.id,
            timestamp=c.started_at,
            title=title,
            summary=c.summary,
            metadata={
                "connected": c.connected,
                "duration_sec": c.duration_sec,
                "sentiment": c.sentiment,
                "agent_name": c.agent_name,
                "counterparty_phone": cp,
            },
        ))

    nq = db.query(Note).filter(Note.shop_url == shop_url)
    if since:
        nq = nq.filter(Note.created_at >= since)
    if until:
        nq = nq.filter(Note.created_at <= until)
    for n in nq.all():
        items.append(TimelineItem(
            type="note",
            id=str(n.id),
            timestamp=n.created_at,
            title=f"Note by {n.author or 'team'}",
            summary=n.body,
            metadata={"is_followup": n.is_followup, "due_at": n.due_at.isoformat() if n.due_at else None},
        ))

    iq = db.query(Issue).filter(Issue.shop_url == shop_url)
    if since:
        iq = iq.filter(Issue.opened_at >= since)
    if until:
        iq = iq.filter(Issue.opened_at <= until)
    for i in iq.all():
        items.append(TimelineItem(
            type="issue",
            id=str(i.id),
            timestamp=i.opened_at,
            title=i.title,
            summary=i.description,
            metadata={"priority": i.priority, "status": i.status, "source": i.source},
        ))

    items.sort(key=lambda x: x.timestamp, reverse=True)
    return items[:limit]
