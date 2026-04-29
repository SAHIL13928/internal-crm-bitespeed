// Typed API client. Mirrors crm_app/schemas.py (Pydantic).
// Override base via ?api=<url> or <meta name="api-base"> for cross-origin deploys.

import type {
  CallDetail, CallListItem, ContactOut, IssueOut, MeetingDetail, MeetingListItem,
  NoteOut, ShopProfile, ShopSummary, TimelineItem, WhatsAppGroupOut, WhatsAppMessage,
} from "./types";

function resolveApiBase(): string {
  const qs = new URLSearchParams(location.search).get("api");
  if (qs) return qs.replace(/\/$/, "");
  if (location.protocol.startsWith("http")) return location.origin;
  const meta = document.querySelector('meta[name="api-base"]');
  if (meta && meta.getAttribute("content")) return meta.getAttribute("content")!.replace(/\/$/, "");
  return "";
}

const API_BASE = resolveApiBase();

async function get<T>(path: string): Promise<T | null> {
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const r = await fetch(API_BASE + path, {
        credentials: "include",
        headers: { Accept: "application/json" },
      });
      if (r.status === 404) return null;
      if (r.status === 401) return null;
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return (await r.json()) as T;
    } catch (err) {
      if (attempt === 0) { await new Promise((r) => setTimeout(r, 250)); continue; }
      console.warn("api get failed", path, err);
      return null;
    }
  }
  return null;
}

async function send<T>(method: "POST" | "PATCH", path: string, body: unknown): Promise<T | null> {
  try {
    const r = await fetch(API_BASE + path, {
      method, credentials: "include",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return (await r.json()) as T;
  } catch (err) {
    console.warn("api send failed", path, err);
    return null;
  }
}

function qstr(params: Record<string, string | number | undefined>): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
  }
  return parts.length ? `?${parts.join("&")}` : "";
}

// Range filter for queries like calls/meetings.
export interface RangeFilter { since?: string; until?: string; limit?: number; }

export const Api = {
  health:     () =>
    get<{ status: string; shops: number; meetings: number; calls: number; calls_with_shop: number; }>("/api/health"),

  merchants:  (q: string, limit = 50) =>
    get<ShopSummary[]>(`/api/merchants${qstr({ q, limit })}`),

  merchant:   (shopUrl: string) =>
    get<ShopProfile>(`/api/merchants/${encodeURIComponent(shopUrl)}`),

  contacts:   (shopUrl: string) =>
    get<ContactOut[]>(`/api/merchants/${encodeURIComponent(shopUrl)}/contacts`),

  whatsapp:   (shopUrl: string) =>
    get<WhatsAppGroupOut[]>(`/api/merchants/${encodeURIComponent(shopUrl)}/whatsapp`),

  waMessages: (shopUrl: string, limit = 200) =>
    get<WhatsAppMessage[]>(`/api/merchants/${encodeURIComponent(shopUrl)}/whatsapp/messages${qstr({ limit })}`),

  meetings:   (shopUrl: string, f: RangeFilter = {}) =>
    get<MeetingListItem[]>(`/api/merchants/${encodeURIComponent(shopUrl)}/meetings${qstr({ since: f.since, until: f.until, limit: f.limit ?? 100 })}`),

  meeting:    (id: string) =>
    get<MeetingDetail>(`/api/meetings/${encodeURIComponent(id)}`),

  calls:      (shopUrl: string, f: RangeFilter = {}) =>
    get<CallListItem[]>(`/api/merchants/${encodeURIComponent(shopUrl)}/calls${qstr({ since: f.since, until: f.until, limit: f.limit ?? 100 })}`),

  call:       (id: string) =>
    get<CallDetail>(`/api/calls/${encodeURIComponent(id)}`),

  issues:     (shopUrl: string, status?: string) =>
    get<IssueOut[]>(`/api/merchants/${encodeURIComponent(shopUrl)}/issues${qstr({ status: status ?? "" })}`),

  notes:      (shopUrl: string) =>
    get<NoteOut[]>(`/api/merchants/${encodeURIComponent(shopUrl)}/notes`),

  timeline:   (shopUrl: string, f: RangeFilter = {}) =>
    get<TimelineItem[]>(`/api/merchants/${encodeURIComponent(shopUrl)}/timeline${qstr({ since: f.since, until: f.until, limit: f.limit ?? 200 })}`),

  createNote: (shopUrl: string, body: { body: string; author?: string; is_followup?: boolean; due_at?: string }) =>
    send<NoteOut>("POST", `/api/merchants/${encodeURIComponent(shopUrl)}/notes`, body),

  createIssue: (shopUrl: string, body: { title: string; description?: string; priority?: "high" | "med" | "low" }) =>
    send<IssueOut>("POST", `/api/merchants/${encodeURIComponent(shopUrl)}/issues`, body),

  patchIssue: (id: number, body: Partial<{ status: "open" | "in_progress" | "resolved"; priority: "high" | "med" | "low"; owner: string }>) =>
    send<IssueOut>("PATCH", `/api/issues/${id}`, body),

  markDnc:    (shopUrl: string, body: { reason: string; note?: string; revisit_on?: string }) =>
    send<ShopSummary>("POST", `/api/merchants/${encodeURIComponent(shopUrl)}/dnc`, body),
};
