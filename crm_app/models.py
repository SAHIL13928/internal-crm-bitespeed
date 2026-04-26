from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from .db import Base


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
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

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
    opened_at = Column(DateTime, default=datetime.utcnow)
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
    created_at = Column(DateTime, default=datetime.utcnow)

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
    received_at = Column(DateTime, default=datetime.utcnow)
    shop_url = Column(String, ForeignKey("shops.shop_url"), nullable=True, index=True)


class WhatsAppGroupEvent(Base):
    __tablename__ = "whatsapp_group_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String, index=True)
    group_id = Column(String, index=True)
    group_name = Column(String, nullable=True)
    members = Column(Text, nullable=True)        # JSON list of {phone, name, is_admin}
    changed_at = Column(DateTime, index=True, nullable=True)
    received_at = Column(DateTime, default=datetime.utcnow)
    raw = Column(Text, nullable=True)
