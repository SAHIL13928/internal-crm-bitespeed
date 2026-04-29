// API client — typed wrapper around fetch.
//
// API base resolution mirrors the prior vanilla-JS logic:
//   1. ?api=<url>           query string override (per-tab debugging)
//   2. window.location.origin   when served over http(s)
//   3. <meta name="api-base">   fallback for cross-origin / file:// previews
//
// Basic auth: `credentials: 'include'` lets the browser persist creds
// after the first 401 → password prompt.

import type {
  CallDetail, CallListItem, ContactOut, IssueOut, MeetingDetail, MeetingListItem,
  NoteOut, ShopProfile, ShopSummary, TimelineItem, WhatsAppGroupOut, WhatsAppMessage,
} from "./types";

function resolveApiBase(): string {
  const qs = new URLSearchParams(location.search).get("api");
  if (qs) return qs.replace(/\/$/, "");
  if (location.protocol.startsWith("http")) return location.origin;
  const meta = document.querySelector('meta[name="api-base"]');
  if (meta && meta.getAttribute("content")) {
    return meta.getAttribute("content")!.replace(/\/$/, "");
  }
  return "https://internal-crm-bitespeed.onrender.com";
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
      if (r.status === 401) {
        // Browser will pop the basic-auth prompt on next interaction.
        return null;
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return (await r.json()) as T;
    } catch (err) {
      if (attempt === 0) {
        await new Promise((r) => setTimeout(r, 250));
        continue;
      }
      console.warn("api get failed", path, err);
      return null;
    }
  }
  return null;
}

async function send<T>(method: "POST" | "PATCH", path: string, body: unknown): Promise<T | null> {
  try {
    const r = await fetch(API_BASE + path, {
      method,
      credentials: "include",
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

// ── typed endpoints ─────────────────────────────────────────────────────
export const Api = {
  health:     ()                                              => get<{ status: string; shops: number }>("/api/health"),
  merchants:  (q: string, limit = 50)                         => get<ShopSummary[]>(`/api/merchants?q=${encodeURIComponent(q)}&limit=${limit}`),
  merchant:   (shopUrl: string)                               => get<ShopProfile>(`/api/merchants/${encodeURIComponent(shopUrl)}`),
  contacts:   (shopUrl: string)                               => get<ContactOut[]>(`/api/merchants/${encodeURIComponent(shopUrl)}/contacts`),
  whatsapp:   (shopUrl: string)                               => get<WhatsAppGroupOut[]>(`/api/merchants/${encodeURIComponent(shopUrl)}/whatsapp`),
  waMessages: (shopUrl: string, limit = 200)                  => get<WhatsAppMessage[]>(`/api/merchants/${encodeURIComponent(shopUrl)}/whatsapp/messages?limit=${limit}`),
  meetings:   (shopUrl: string, limit = 100)                  => get<MeetingListItem[]>(`/api/merchants/${encodeURIComponent(shopUrl)}/meetings?limit=${limit}`),
  meeting:    (id: string)                                    => get<MeetingDetail>(`/api/meetings/${encodeURIComponent(id)}`),
  calls:      (shopUrl: string, limit = 100)                  => get<CallListItem[]>(`/api/merchants/${encodeURIComponent(shopUrl)}/calls?limit=${limit}`),
  call:       (id: string)                                    => get<CallDetail>(`/api/calls/${encodeURIComponent(id)}`),
  issues:     (shopUrl: string)                               => get<IssueOut[]>(`/api/merchants/${encodeURIComponent(shopUrl)}/issues`),
  notes:      (shopUrl: string)                               => get<NoteOut[]>(`/api/merchants/${encodeURIComponent(shopUrl)}/notes`),
  timeline:   (shopUrl: string, limit = 200)                  => get<TimelineItem[]>(`/api/merchants/${encodeURIComponent(shopUrl)}/timeline?limit=${limit}`),
  createNote: (shopUrl: string, body: { body: string; author?: string; is_followup?: boolean; due_at?: string }) =>
                                                                  send<NoteOut>("POST", `/api/merchants/${encodeURIComponent(shopUrl)}/notes`, body),
  markDnc:    (shopUrl: string, body: { reason: string; note?: string; revisit_on?: string }) =>
                                                                  send<ShopSummary>("POST", `/api/merchants/${encodeURIComponent(shopUrl)}/dnc`, body),
};
