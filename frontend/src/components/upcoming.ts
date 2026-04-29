// "Upcoming" tab — future meetings + scheduled calls.
// Backend currently surfaces upcoming meetings (extracted from WA invites).
// Calls are historical only, so we surface "recent attempts" as a fallback
// section so the tab isn't empty when there are no meetings on the books.
import { Api } from "../api";
import type { ShopProfile, MeetingListItem, CallListItem } from "../types";
import { $, avatarStack, emptyBlock, esc, fmtDayLabel, fmtRelative, directionPill, connectedDot } from "../utils";

export async function renderUpcoming(p: ShopProfile): Promise<void> {
  const body = $("#tab-body");
  if (!body) return;
  body.innerHTML = `<div class="text-xs text-slate-500">Loading…</div>`;

  // Meetings — fetch all, then split by date relative to now.
  const allMtgs = (await Api.meetings(p.shop_url, { limit: 200 })) || [];
  const now = Date.now();
  const upcoming = allMtgs
    .filter((m) => m.date && new Date(m.date).getTime() > now)
    .sort((a, b) => new Date(a.date!).getTime() - new Date(b.date!).getTime());

  // Recent calls (last 7d), as a "Upcoming activity" stand-in
  const since = new Date(now - 7 * 86400 * 1000).toISOString();
  const recentCalls = (await Api.calls(p.shop_url, { since, limit: 30 })) || [];

  const meetingsBlock = upcoming.length === 0
    ? emptyBlock("No upcoming meetings on the books.")
    : `<div class="space-y-2">${upcoming.map(renderMeetingCard).join("")}</div>`;

  const callsBlock = recentCalls.length === 0
    ? emptyBlock("No recent call attempts.")
    : `<div class="space-y-2">${recentCalls.slice(0, 8).map(renderCallCard).join("")}</div>`;

  body.innerHTML = `
    <section class="mb-6">
      <div class="flex items-baseline justify-between mb-3">
        <h3 class="text-sm font-semibold">Upcoming meetings</h3>
        <div class="text-xs text-slate-500">${upcoming.length} scheduled</div>
      </div>
      ${meetingsBlock}
    </section>

    <section>
      <div class="flex items-baseline justify-between mb-3">
        <h3 class="text-sm font-semibold">Recent call attempts</h3>
        <div class="text-xs text-slate-500">last 7 days · ${recentCalls.length}</div>
      </div>
      ${callsBlock}
    </section>
  `;
}

function renderMeetingCard(m: MeetingListItem): string {
  const initialsHtml = avatarStack(m.attendee_initials || []);
  return `
    <div class="card p-3">
      <div class="flex items-start justify-between gap-3">
        <div class="flex-1">
          <div class="text-xs text-slate-500 mb-1">${esc(fmtDayLabel(m.date))}</div>
          <div class="text-sm font-medium">${esc(m.title)}</div>
          ${m.summary_short ? `<div class="text-xs text-slate-600 mt-1 line-clamp-2">${esc(m.summary_short)}</div>` : ""}
        </div>
        <div class="flex items-center pr-1">${initialsHtml}</div>
      </div>
    </div>
  `;
}

function renderCallCard(c: CallListItem): string {
  const counterpartyLabel = c.counterparty_name || c.counterparty_phone || "—";
  return `
    <div class="card p-3 text-sm">
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-2">
          ${connectedDot(c.connected)}
          ${directionPill(c.direction)}
          <span class="font-medium">${esc(counterpartyLabel)}</span>
        </div>
        <div class="text-xs text-slate-500">${esc(fmtRelative(c.started_at))}</div>
      </div>
      ${c.summary_text ? `<div class="text-xs text-slate-600 mt-2 line-clamp-2">${esc(c.summary_text)}</div>` : ""}
    </div>
  `;
}
