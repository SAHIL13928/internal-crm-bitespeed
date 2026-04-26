from datetime import date, datetime
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ── shared ────────────────────────────────────────────────────────────────
class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ── contacts / whatsapp ───────────────────────────────────────────────────
class ContactOut(ORM):
    id: int
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    is_internal: bool = False
    role: Optional[str] = None


class WhatsAppGroupOut(ORM):
    id: int
    group_name: str
    last_activity_at: Optional[datetime] = None


# ── shops ─────────────────────────────────────────────────────────────────
class ShopSummary(ORM):
    shop_url: str
    brand_name: Optional[str] = None
    health_status: str = "unknown"
    outreach_status: str = "open"
    account_manager: Optional[str] = None


class ShopKpi(BaseModel):
    upcoming_meetings: int = 0
    calls_7d_attempted: int = 0
    calls_7d_connected: int = 0
    meetings_7d: int = 0
    open_issues: int = 0
    whatsapp_groups: int = 0


class ShopProfile(ORM):
    shop_url: str
    brand_name: Optional[str] = None
    health_status: str
    outreach_status: str
    dnc_reason: Optional[str] = None
    dnc_revisit_on: Optional[date] = None
    account_manager: Optional[str] = None
    confidence: Optional[str] = None
    contacts: List[ContactOut] = []
    whatsapp_groups: List[WhatsAppGroupOut] = []
    kpi: ShopKpi = ShopKpi()


# ── meetings ──────────────────────────────────────────────────────────────
class AttendeeOut(ORM):
    email: Optional[str] = None
    display_name: Optional[str] = None
    is_internal: bool = False


class MeetingListItem(ORM):
    id: str
    title: str
    date: Optional[datetime] = None
    duration_min: Optional[float] = None
    summary_short: Optional[str] = None
    shop_url: Optional[str] = None
    mapping_source: Optional[str] = None


class MeetingDetail(MeetingListItem):
    organizer_email: Optional[str] = None
    host_email: Optional[str] = None
    meeting_link: Optional[str] = None
    transcript_url: Optional[str] = None
    audio_url: Optional[str] = None
    video_url: Optional[str] = None
    summary_overview: Optional[str] = None
    summary_bullet_gist: Optional[str] = None
    summary_keywords: Optional[List[str]] = None
    action_items: Optional[str] = None
    attendees: List[AttendeeOut] = []


# ── calls ─────────────────────────────────────────────────────────────────
class CallListItem(ORM):
    id: str
    started_at: Optional[datetime] = None
    direction: Optional[str] = None
    connected: bool = False
    duration_sec: Optional[int] = None
    from_number: Optional[str] = None
    to_number: Optional[str] = None
    agent_name: Optional[str] = None
    summary: Optional[str] = None
    sentiment: Optional[str] = None
    shop_url: Optional[str] = None


class CallDetail(CallListItem):
    agent_email: Optional[str] = None
    recording_url: Optional[str] = None
    transcript: Optional[str] = None


# ── issues ────────────────────────────────────────────────────────────────
class IssueOut(ORM):
    id: int
    shop_url: str
    title: str
    description: Optional[str] = None
    priority: str
    status: str
    source: Optional[str] = None
    source_ref: Optional[str] = None
    owner: Optional[str] = None
    jira_ticket_id: Optional[str] = None
    opened_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None


class IssueCreate(BaseModel):
    title: str
    description: Optional[str] = None
    priority: Literal["high", "med", "low"] = "med"
    source: Optional[Literal["whatsapp", "call", "meeting", "manual"]] = "manual"
    source_ref: Optional[str] = None
    owner: Optional[str] = None
    jira_ticket_id: Optional[str] = None


class IssuePatch(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[Literal["high", "med", "low"]] = None
    status: Optional[Literal["open", "in_progress", "resolved"]] = None
    owner: Optional[str] = None
    jira_ticket_id: Optional[str] = None


# ── notes ─────────────────────────────────────────────────────────────────
class NoteOut(ORM):
    id: int
    shop_url: str
    author: Optional[str] = None
    body: str
    is_followup: bool = False
    due_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class NoteCreate(BaseModel):
    body: str
    author: Optional[str] = None
    is_followup: bool = False
    due_at: Optional[datetime] = None


# ── DNC ───────────────────────────────────────────────────────────────────
class DNCRequest(BaseModel):
    reason: Literal["Churned", "Acquisition", "Legal hold", "Customer request", "Other"]
    note: Optional[str] = None
    revisit_on: Optional[date] = None


# ── timeline ──────────────────────────────────────────────────────────────
class TimelineItem(BaseModel):
    type: Literal["meeting", "call", "note", "issue"]
    id: str
    timestamp: datetime
    title: str
    summary: Optional[str] = None
    metadata: dict = {}


# ── whatsapp webhooks ─────────────────────────────────────────────────────
class WhatsAppMessageIn(BaseModel):
    # required per the spec we sent the intern
    group_name: str
    sender_phone: str
    sender_name: str
    timestamp: datetime
    is_from_me: bool
    message_type: str           # "text" | "document" (kept open; server tolerates extras)

    # at least one of body / media_url should be present in practice
    body: Optional[str] = None
    media_url: Optional[str] = None

    # optional — server does not require, but will use if the bridge sends them
    message_id: Optional[str] = None        # if absent, server derives a stable hash
    group_id: Optional[str] = None          # WA JID; if absent, we bind by group_name
    media_mime_type: Optional[str] = None
    media_caption: Optional[str] = None
    reply_to_message_id: Optional[str] = None
    is_edited: bool = False
    is_deleted: bool = False
    raw: Optional[dict] = None


class WhatsAppMessageBatch(BaseModel):
    messages: List[WhatsAppMessageIn] = Field(..., min_length=1, max_length=1000)


class WhatsAppGroupMember(BaseModel):
    phone: Optional[str] = None
    name: Optional[str] = None
    is_admin: bool = False


class WhatsAppGroupEventIn(BaseModel):
    event_type: Literal["group_created", "group_renamed", "members_added", "members_removed"]
    group_id: str
    group_name: Optional[str] = None
    members: List[WhatsAppGroupMember] = []
    changed_at: datetime
    raw: Optional[dict] = None


class WhatsAppMessageFailure(BaseModel):
    message_id: str
    error: str


class WhatsAppMessagesResult(BaseModel):
    received: int
    inserted: int
    updated: int
    failed: List[WhatsAppMessageFailure] = []
    accepted_ids: List[str] = []


class WhatsAppGroupEventResult(BaseModel):
    event_id: int
    group_jid: str
    applied: bool
