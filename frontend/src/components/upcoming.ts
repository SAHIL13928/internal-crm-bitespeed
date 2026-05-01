// "Upcoming" tab — Google-Calendar-sourced future meetings + recent calls.
// Data flow: scripts/sync_google_calendars.py upserts CalendarEvent rows
// every 10 min on the EC2 sidecar; this component reads
// /api/shops/{shop_url}/upcoming-meetings.
//
// The Fireflies-backed Meetings table holds *historic* recordings only
// (Fireflies records past calls) — filtering it for "future" rows
// always returned empty, which is why the Upcoming tab was blank before
// the calendar integration landed.
import { Api } from "../api";
import type { CallListItem, ShopProfile, UpcomingMeeting } from "../types";
import { $, avatarStack, esc, fmtDayLabel, fmtRelative, initials } from "../utils";

const INTERNAL_DOMAINS = new Set(["bitespeed.co"]);

export async function renderUpcoming(p: ShopProfile): Promise<void> {
  const body = $("#tab-body");
  if (!body) return;
  body.innerHTML = `<div class="text-xs text-ink-500">Loading…</div>`;

  const [upcoming, recentCalls] = await Promise.all([
    Api.upcomingMeetings(p.shop_url, 20),
    Api.calls(p.shop_url, {
      since: new Date(Date.now() - 7 * 86400 * 1000).toISOString(),
      limit: 10,
    }),
  ]);

  const upcomingList = upcoming || [];
  const callsList = recentCalls || [];

  body.innerHTML = `
    <section>
      <div class="flex items-baseline justify-between mb-4">
        <h2 class="text-base font-semibold">Upcoming meetings</h2>
        <span class="text-xs text-ink-500">${upcomingList.length} scheduled</span>
      </div>
      ${upcomingList.length === 0
        ? emptyCard()
        : timeline(upcomingList.map(mtgItem))}
    </section>

    <section class="mt-10">
      <div class="flex items-baseline justify-between mb-4">
        <h2 class="text-base font-semibold">Recent call attempts</h2>
        <span class="text-xs text-ink-500">last 7 days · ${callsList.length}</span>
      </div>
      ${callsList.length === 0
        ? `<div class="rounded-lg border border-gray-200 bg-white p-5 text-sm text-ink-500 italic">No call attempts in the last 7 days.</div>`
        : timeline(callsList.map(callItem))}
    </section>
  `;
}

function timeline(items: string[]): string {
  return `<ol class="relative border-l border-gray-200 ml-2 space-y-4 pl-6">${items.join("")}</ol>`;
}

// Empty state nudges the operator toward the connect flow when nothing
// has been synced yet — the most common cause of an empty list on day 1
// is "nobody clicked Connect calendar."
function emptyCard(): string {
  return `
    <div class="rounded-lg border border-gray-200 bg-white p-5 text-sm text-ink-500">
      <div class="italic">No upcoming meetings on the books for this merchant.</div>
      <div class="mt-2 text-xs">
        Calendar events are synced every ~10&nbsp;min from connected Google
        accounts. If this looks wrong,
        <a href="/auth/google/connect" class="text-indigo-600 underline">connect your calendar</a>
        or check
        <a href="/auth/google/connections" class="text-indigo-600 underline">connections</a>.
      </div>
    </div>
  `;
}

function mtgItem(m: UpcomingMeeting): string {
  const dotColor = isToday(m.start_time) ? "bg-indigo-500" : "bg-gray-300";
  // Attendee initials — exclude internal Bitespeed addresses so the
  // stack actually shows the merchant-side participants.
  const externalNames = (m.attendee_emails || [])
    .map((a) => a.email)
    .filter((e): e is string => Boolean(e) && !isInternal(e))
    .map(emailToDisplay);
  const stack = avatarStack(externalNames);
  const meetLink = m.meeting_link
    ? `<a href="${esc(m.meeting_link)}" target="_blank" rel="noopener" class="text-xs text-indigo-600 underline mt-1 inline-block">Join</a>`
    : "";
  return `
    <li class="relative">
      <span class="absolute -left-[31px] top-1.5 w-3 h-3 rounded-full ${dotColor} ring-4 ring-white"></span>
      <div class="flex items-start justify-between gap-4 p-4 rounded-lg border border-gray-200 bg-white hover:border-indigo-300">
        <div class="min-w-0">
          <div class="text-xs text-ink-500">${esc(fmtDayLabel(m.start_time))}</div>
          <div class="text-sm font-medium mt-0.5 truncate">${esc(m.summary || "(untitled)")}</div>
          ${m.description ? `<div class="text-xs text-ink-500 mt-1 line-clamp-2">${esc(m.description)}</div>` : ""}
          ${meetLink}
        </div>
        ${stack}
      </div>
    </li>
  `;
}

function callItem(c: CallListItem): string {
  const dotColor = c.connected ? "bg-emerald-500" : "bg-gray-300";
  const counterparty = c.counterparty_name || c.counterparty_phone || "—";
  const dirLabel = (c.direction || "").startsWith("out") ? "outbound" : "inbound";
  return `
    <li class="relative">
      <span class="absolute -left-[31px] top-1.5 w-3 h-3 rounded-full ${dotColor} ring-4 ring-white"></span>
      <div class="flex items-start justify-between gap-4 p-4 rounded-lg border border-gray-200 bg-white hover:border-indigo-300 cursor-pointer">
        <div class="min-w-0">
          <div class="text-xs text-ink-500">${esc(fmtRelative(c.started_at))}</div>
          <div class="text-sm font-medium mt-0.5 truncate">${esc(counterparty)}</div>
          ${c.summary_text ? `<div class="text-xs text-ink-500 mt-1 line-clamp-2">${esc(c.summary_text)}</div>` : ""}
        </div>
        <div class="text-xs text-ink-500 shrink-0">${c.agent_name ? esc(c.agent_name) + " · " : ""}${esc(dirLabel)}</div>
      </div>
    </li>
  `;
}

function isToday(iso: string | null | undefined): boolean {
  if (!iso) return false;
  const d = new Date(iso);
  const now = new Date();
  return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
}

function isInternal(email: string): boolean {
  const dom = email.toLowerCase().split("@")[1];
  return Boolean(dom) && INTERNAL_DOMAINS.has(dom);
}

// "alice.smith@example.com" → "Alice Smith" — best effort label so
// avatarStack can produce something meaningful when we don't have a
// display name on the attendee.
function emailToDisplay(email: string): string {
  const local = email.split("@")[0] || email;
  const cleaned = local.replace(/[._-]+/g, " ").trim();
  if (!cleaned) return email;
  return cleaned.replace(/\b\w/g, (c) => c.toUpperCase());
}

// Re-exported for callers that want to derive initials from an email.
export const _internalForTests = { isInternal, emailToDisplay, initials };
