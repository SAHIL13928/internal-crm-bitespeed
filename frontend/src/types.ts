// API types — kept in sync with crm_app/schemas.py (Pydantic models).
// If the backend schema changes, mirror the change here.

export interface ShopSummary {
  shop_url: string;
  brand_name: string | null;
  health_status: string;
  outreach_status: string;
  account_manager: string | null;
}

export interface ContactOut {
  id: number;
  name: string | null;
  email: string | null;
  phone: string | null;
  is_internal: boolean;
  role: string | null;
}

export interface WhatsAppGroupOut {
  id: number;
  group_name: string;
  last_activity_at: string | null;
}

export interface ShopKpi {
  upcoming_meetings: number;
  calls_7d_attempted: number;
  calls_7d_connected: number;
  calls_7d_connect_rate_pct: number | null;
  meetings_7d: number;
  meetings_7d_total_minutes: number | null;
  open_issues: number;
  whatsapp_groups: number;
  last_contact_at: string | null;
  last_contact_kind: string | null;
}

export interface ShopProfile {
  shop_url: string;
  brand_name: string | null;
  health_status: string;
  outreach_status: string;
  dnc_reason: string | null;
  dnc_revisit_on: string | null;
  account_manager: string | null;
  confidence: string | null;
  contacts: ContactOut[];
  whatsapp_groups: WhatsAppGroupOut[];
  kpi: ShopKpi;
}

export interface MeetingListItem {
  id: string;
  title: string;
  date: string | null;
  duration_min: number | null;
  summary_short: string | null;
  shop_url: string | null;
  mapping_source: string | null;
  attendee_count: number | null;
  attendee_initials: string[] | null;
}

export interface AttendeeOut {
  email: string | null;
  display_name: string | null;
  is_internal: boolean;
}

export interface MeetingDetail extends MeetingListItem {
  organizer_email: string | null;
  host_email: string | null;
  meeting_link: string | null;
  transcript_url: string | null;
  audio_url: string | null;
  video_url: string | null;
  summary_overview: string | null;
  summary_bullet_gist: string | null;
  summary_keywords: string[] | null;
  action_items: string | null;
  attendees: AttendeeOut[];
}

export interface CallListItem {
  id: string;
  started_at: string | null;
  direction: string | null;
  connected: boolean;
  duration_sec: number | null;
  from_number: string | null;
  to_number: string | null;
  agent_name: string | null;
  counterparty_phone: string | null;
  counterparty_name: string | null;
  counterparty_role: string | null;
  summary: string | null;
  summary_text: string | null;
  ai_insights: unknown | null;
  sentiment: string | null;
  shop_url: string | null;
}

export interface CallDetail extends CallListItem {
  agent_email: string | null;
  recording_url: string | null;
  transcript: string | null;
  transcript_segments: unknown | null;
}

export interface IssueOut {
  id: number;
  shop_url: string;
  title: string;
  description: string | null;
  priority: string;
  status: string;
  source: string | null;
  source_ref: string | null;
  owner: string | null;
  jira_ticket_id: string | null;
  opened_at: string | null;
  resolved_at: string | null;
}

export interface NoteOut {
  id: number;
  shop_url: string;
  author: string | null;
  body: string;
  is_followup: boolean;
  due_at: string | null;
  created_at: string | null;
}

export interface TimelineItem {
  type: "meeting" | "call" | "note" | "issue";
  id: string;
  timestamp: string;
  title: string;
  summary: string | null;
  metadata: Record<string, unknown>;
}

export interface WhatsAppMessage {
  id: number;
  group_name: string;
  sender_phone: string;
  sender_name: string | null;
  timestamp: string | null;
  body: string;
  is_from_me: boolean;
  message_type: string;
  media_url: string | null;
  is_edited: boolean;
  is_deleted: boolean;
  resolution_method: string | null;
}

// Google Calendar — populated by scripts/sync_google_calendars.py and
// exposed via /api/shops/{shop_url}/upcoming-meetings. Distinct from
// MeetingListItem (Fireflies — historic recordings only).
export interface UpcomingMeeting {
  id: number;
  google_event_id: string;
  summary: string | null;
  description: string | null;
  start_time: string | null;
  end_time: string | null;
  meeting_link: string | null;
  organizer_email: string | null;
  attendee_emails: { email: string; response_status?: string | null }[];
}

export interface CalendarConnection {
  id: number;
  user_email: string;
  auth_mode: "user_oauth" | "dwd_impersonation";
  status: "active" | "failing" | "revoked";
  last_synced_at: string | null;
  last_error: string | null;
  created_at: string | null;
}

// API response wrapper — `null` = network/404, `__unauthorized` =
// surface auth flow. The fetch wrapper returns one of these.
export type ApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; status: "notfound" | "unauthorized" | "error" };
