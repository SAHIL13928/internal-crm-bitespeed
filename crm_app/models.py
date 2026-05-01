from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .db import Base
from .time_utils import utcnow_naive


class Shop(Base):
    __tablename__ = "shops"

    shop_url = Column(String, primary_key=True)
    brand_name = Column(String, nullable=True)
    health_status = Column(String, default="unknown")   # at_risk | healthy | dnc | unknown
    outreach_status = Column(String, default="open")    # open | dnc
    dnc_reason = Column(String, nullable=True)
    dnc_note = Column(Text, nullable=True)
    dnc_revisit_on = Column(Date, nullable=True)
    account_manager = Column(String, nullable=True)
    confidence = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)

    contacts = relationship("Contact", back_populates="shop", cascade="all, delete-orphan")
    whatsapp_groups = relationship("WhatsAppGroup", back_populates="shop", cascade="all, delete-orphan")
    meetings = relationship("Meeting", back_populates="shop")
    calls = relationship("Call", back_populates="shop")
    issues = relationship("Issue", back_populates="shop", cascade="all, delete-orphan")
    notes = relationship("Note", back_populates="shop", cascade="all, delete-orphan")


class Contact(Base):
    __tablename__ = "contacts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    shop_url = Column(String, ForeignKey("shops.shop_url"), index=True)
    name = Column(String, nullable=True)
    email = Column(String, nullable=True, index=True)
    phone = Column(String, nullable=True, index=True)
    is_internal = Column(Boolean, default=False)
    role = Column(String, nullable=True)

    shop = relationship("Shop", back_populates="contacts")


class WhatsAppGroup(Base):
    __tablename__ = "whatsapp_groups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    shop_url = Column(String, ForeignKey("shops.shop_url"), nullable=True, index=True)
    group_jid = Column(String, nullable=True, index=True)
    group_name = Column(String)
    last_activity_at = Column(DateTime, nullable=True)

    shop = relationship("Shop", back_populates="whatsapp_groups")


class Meeting(Base):
    __tablename__ = "meetings"

    id = Column(String, primary_key=True)  # Fireflies ULID
    shop_url = Column(String, ForeignKey("shops.shop_url"), nullable=True, index=True)
    title = Column(String)
    date = Column(DateTime, index=True)
    duration_min = Column(Float, nullable=True)
    organizer_email = Column(String, nullable=True)
    host_email = Column(String, nullable=True)
    meeting_link = Column(String, nullable=True)
    transcript_url = Column(String, nullable=True)
    audio_url = Column(Text, nullable=True)
    video_url = Column(Text, nullable=True)
    summary_short = Column(Text, nullable=True)
    summary_overview = Column(Text, nullable=True)
    summary_bullet_gist = Column(Text, nullable=True)
    summary_keywords = Column(Text, nullable=True)   # JSON-encoded list
    action_items = Column(Text, nullable=True)
    mapping_source = Column(String, nullable=True)   # link | email | none

    shop = relationship("Shop", back_populates="meetings")
    attendees = relationship("MeetingAttendee", back_populates="meeting", cascade="all, delete-orphan")


class MeetingAttendee(Base):
    __tablename__ = "meeting_attendees"

    id = Column(Integer, primary_key=True, autoincrement=True)
    meeting_id = Column(String, ForeignKey("meetings.id"), index=True)
    email = Column(String, nullable=True, index=True)
    display_name = Column(String, nullable=True)
    is_internal = Column(Boolean, default=False)

    meeting = relationship("Meeting", back_populates="attendees")


class Call(Base):
    __tablename__ = "calls"

    id = Column(String, primary_key=True)            # Frejun call uuid (or generated)
    shop_url = Column(String, ForeignKey("shops.shop_url"), nullable=True, index=True)
    direction = Column(String)                       # inbound | outbound
    connected = Column(Boolean, default=False)
    started_at = Column(DateTime, index=True)
    duration_sec = Column(Integer, nullable=True)
    from_number = Column(String, nullable=True)
    to_number = Column(String, nullable=True)
    agent_email = Column(String, nullable=True)
    agent_name = Column(String, nullable=True)
    recording_url = Column(Text, nullable=True)
    transcript = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    sentiment = Column(String, nullable=True)        # happy | neutral | concerned | frustrated
    raw = Column(Text, nullable=True)                # original JSON blob, in case

    shop = relationship("Shop", back_populates="calls")


class Issue(Base):
    __tablename__ = "issues"

    id = Column(Integer, primary_key=True, autoincrement=True)
    shop_url = Column(String, ForeignKey("shops.shop_url"), index=True)
    title = Column(String)
    description = Column(Text, nullable=True)
    priority = Column(String, default="med")         # high | med | low
    status = Column(String, default="open")          # open | in_progress | resolved
    source = Column(String, nullable=True)           # whatsapp | call | meeting | manual
    source_ref = Column(String, nullable=True)       # related call/meeting id
    owner = Column(String, nullable=True)
    jira_ticket_id = Column(String, nullable=True)
    opened_at = Column(DateTime, default=utcnow_naive)
    resolved_at = Column(DateTime, nullable=True)

    shop = relationship("Shop", back_populates="issues")


class Note(Base):
    __tablename__ = "notes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    shop_url = Column(String, ForeignKey("shops.shop_url"), index=True)
    author = Column(String, nullable=True)
    body = Column(Text)
    is_followup = Column(Boolean, default=False)
    due_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow_naive)

    shop = relationship("Shop", back_populates="notes")


class WhatsAppMessage(Base):
    __tablename__ = "whatsapp_messages"

    message_id = Column(String, primary_key=True)
    group_id = Column(String, index=True)
    group_name = Column(String, nullable=True)
    sender_phone = Column(String, index=True, nullable=True)
    sender_name = Column(String, nullable=True)
    timestamp = Column(DateTime, index=True)
    body = Column(Text, nullable=True)
    is_from_me = Column(Boolean, default=False)
    message_type = Column(String, nullable=True)
    reply_to_message_id = Column(String, nullable=True, index=True)
    media_url = Column(Text, nullable=True)
    media_mime_type = Column(String, nullable=True)
    media_caption = Column(Text, nullable=True)
    is_edited = Column(Boolean, default=False)
    is_deleted = Column(Boolean, default=False)
    raw = Column(Text, nullable=True)
    received_at = Column(DateTime, default=utcnow_naive)
    shop_url = Column(String, ForeignKey("shops.shop_url"), nullable=True, index=True)


class WhatsAppGroupEvent(Base):
    __tablename__ = "whatsapp_group_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String, index=True)
    group_id = Column(String, index=True)
    group_name = Column(String, nullable=True)
    members = Column(Text, nullable=True)        # JSON list of {phone, name, is_admin}
    changed_at = Column(DateTime, index=True, nullable=True)
    received_at = Column(DateTime, default=utcnow_naive)
    raw = Column(Text, nullable=True)


# ── Raw landing table for the WA bridge intern's payload ──────────────────
# Distinct from WhatsAppMessage above: this is the canonical intake surface
# the intern POSTs to. Resolution to a shop happens after insert and is
# tracked via resolution_status so the graph reprocessor can revisit pending
# rows once new bindings appear.
class WhatsAppRawMessage(Base):
    __tablename__ = "whatsapp_raw_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Intern-supplied fields (per spec)
    group_name = Column(String, nullable=False, index=True)
    sender_phone = Column(String, nullable=False, index=True)  # E.164
    sender_name = Column(String, nullable=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    body = Column(Text, nullable=False, default="")  # coerced from None to "" so SQLite UNIQUE works (NULLs are distinct in SQLite)
    is_from_me = Column(Boolean, nullable=False, default=False)
    message_type = Column(String, nullable=False)  # text|document
    media_url = Column(Text, nullable=True)

    # Provider-side stable id (Periskope's message_id when available).
    # Used to look up rows for `message.updated` / `message.deleted` events
    # — natural-key dedupe alone can't track edits because body changes.
    source_message_id = Column(String, nullable=True, index=True)

    # Edit / delete state (Periskope event-driven).
    is_edited = Column(Boolean, nullable=False, default=False)
    edited_at = Column(DateTime, nullable=True)
    is_deleted = Column(Boolean, nullable=False, default=False)
    deleted_at = Column(DateTime, nullable=True)

    # Server-side bookkeeping
    received_at = Column(DateTime, default=utcnow_naive, nullable=False)
    processed_at = Column(DateTime, nullable=True)
    resolution_status = Column(String, nullable=False, default="pending", index=True)
    resolved_shop_url = Column(String, ForeignKey("shops.shop_url"), nullable=True, index=True)
    resolution_method = Column(String, nullable=True)

    __table_args__ = (
        # Idempotency for intern retries — same logical message → same row.
        UniqueConstraint("group_name", "sender_phone", "timestamp", "body",
                         name="uq_whatsapp_raw_messages_natural_key"),
        CheckConstraint(
            "resolution_status IN ('pending','resolved','unresolvable','conflict')",
            name="ck_whatsapp_raw_messages_resolution_status",
        ),
    )


# ── Identity graph ─────────────────────────────────────────────────────────
# Nodes are typed (shop_url, phone, email, meeting_link, group_name).
# Edges record co-occurrence in real events with a source + evidence pointer.
# Connected components = same merchant. We never add edges from string
# fuzziness — only from observed co-occurrence.
class Identity(Base):
    __tablename__ = "identities"

    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(String, nullable=False)
    value = Column(String, nullable=False)

    __table_args__ = (
        UniqueConstraint("kind", "value", name="uq_identities_kind_value"),
        CheckConstraint(
            "kind IN ('shop_url','phone','email','meeting_link','group_name')",
            name="ck_identities_kind",
        ),
        Index("ix_identities_kind_value", "kind", "value"),
    )


class Binding(Base):
    __tablename__ = "bindings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Stored undirected with a_id < b_id so each edge appears exactly once.
    identity_a_id = Column(Integer, ForeignKey("identities.id"), nullable=False, index=True)
    identity_b_id = Column(Integer, ForeignKey("identities.id"), nullable=False, index=True)
    source = Column(String, nullable=False)  # static_directory|whatsapp|frejun|fireflies|manual
    confidence = Column(Float, nullable=False, default=1.0)
    observed_at = Column(DateTime, nullable=False, default=utcnow_naive)
    evidence_table = Column(String, nullable=True)
    evidence_id = Column(String, nullable=True)

    __table_args__ = (
        # NULL evidence_id is treated as distinct in SQLite, but we always
        # supply an evidence_id (or a deterministic stand-in) when adding
        # bindings — see crm_app.identity.add_binding.
        UniqueConstraint(
            "identity_a_id", "identity_b_id", "source", "evidence_id",
            name="uq_bindings_natural_key",
        ),
        CheckConstraint("identity_a_id < identity_b_id", name="ck_bindings_undirected_order"),
    )


# ── Google Calendar integration ─────────────────────────────────────────
# Two auth modes share one schema. Refresh tokens are stored encrypted
# (Fernet); see crm_app.google.crypto.
class CalendarConnection(Base):
    __tablename__ = "calendar_connections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_email = Column(String, unique=True, nullable=False)
    auth_mode = Column(String, nullable=False, default="user_oauth")
    # Fernet ciphertext of the user's refresh token. NULL on DWD
    # connections (service account doesn't need a per-user token).
    refresh_token_encrypted = Column(Text, nullable=True)
    access_token = Column(Text, nullable=True)
    token_expires_at = Column(DateTime, nullable=True)
    last_synced_at = Column(DateTime, nullable=True)
    status = Column(String, nullable=False, default="active")  # active|failing|revoked
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow_naive, nullable=False)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False)

    events = relationship("CalendarEvent", back_populates="connection", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(
            "auth_mode IN ('user_oauth','dwd_impersonation')",
            name="ck_calendar_connections_auth_mode",
        ),
        CheckConstraint(
            "status IN ('active','failing','revoked')",
            name="ck_calendar_connections_status",
        ),
    )


class CalendarEvent(Base):
    __tablename__ = "calendar_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    google_event_id = Column(String, nullable=False, index=True)
    connection_id = Column(Integer, ForeignKey("calendar_connections.id"), nullable=False, index=True)

    summary = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    start_time = Column(DateTime, nullable=False, index=True)
    end_time = Column(DateTime, nullable=True)
    meeting_link = Column(Text, nullable=True)
    # JSON-encoded list of {email, response_status} dicts. SQLAlchemy's
    # JSON type maps to TEXT on SQLite and JSONB on Postgres automatically.
    attendee_emails = Column(JSON, nullable=True)
    organizer_email = Column(String, nullable=True)

    shop_url = Column(String, ForeignKey("shops.shop_url"), nullable=True, index=True)
    resolution_status = Column(String, nullable=False, default="pending")
    raw_payload = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=utcnow_naive, nullable=False)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False)

    connection = relationship("CalendarConnection", back_populates="events")

    __table_args__ = (
        # One event per (connection, google id). Reruns of the sync upsert.
        UniqueConstraint("connection_id", "google_event_id",
                         name="uq_calendar_events_connection_event"),
        Index("ix_calendar_events_shop_url_start_time", "shop_url", "start_time"),
        CheckConstraint(
            "resolution_status IN ('pending','resolved','unresolvable','conflict')",
            name="ck_calendar_events_resolution_status",
        ),
    )
