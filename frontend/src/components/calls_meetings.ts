// "Calls & Meetings" tab — stats summary + connect-rate bar + per-row list.
// Layout from the mockup: stats on left, progress bar on right, divided list.
import { Api } from "../api";
import type { CallListItem, MeetingListItem, ShopProfile } from "../types";
import { $, avatarStack, esc, fmtDuration, fmtShortDay, fmtTinyTime, rangeBounds } from "../utils";
import type { RangeKey } from "../utils";
import { bindTimeFilter, renderTimeFilter } from "./time_filter";

let activeRange: RangeKey = "7d";

export async function renderCallsMeetings(p: ShopProfile): Promise<void> {
  const body = $("#tab-body");
  if (!body) return;
  body.innerHTML = wrap("<div class=\"text-xs text-ink-500\">Loading…</div>");
  bindFilter(p);

  const f = rangeBounds(activeRange);
  const [calls, meetings] = await Promise.all([
    Api.calls(p.shop_url, { ...f, limit: 100 }),
    Api.meetings(p.shop_url, { ...f, limit: 100 }),
  ]);

  body.innerHTML = wrap(callsBlock(calls || []) + meetingsBlock(meetings || []));
  bindFilter(p);
}

function wrap(inner: string): string {
  return `
    <div class="flex items-center justify-between mb-6">
      <h2 class="text-base font-semibold">Calls &amp; meetings</h2>
      ${renderTimeFilter("tf-cm", activeRange)}
    </div>
    ${inner}
  `;
}

function bindFilter(p: ShopProfile) {
  bindTimeFilter("tf-cm", (k) => { activeRange = k; void renderCallsMeetings(p); });
}

// ── Calls section ───────────────────────────────────────────────────────
function callsBlock(calls: CallListItem[]): string {
  const total = calls.length;
  const connected = calls.filter((c) => c.connected).length;
  const missed = total - connected;
  const rate = total > 0 ? Math.round((100 * connected) / total) : 0;

  const summaryRow = `
    <div class="flex items-center justify-between gap-6 mb-4">
      <div class="flex items-baseline gap-6">
        <div><div class="text-2xl font-semibold">${total}</div><div class="text-xs text-ink-500">Attempted</div></div>
        <div><div class="text-2xl font-semibold text-emerald-600">${connected}</div><div class="text-xs text-ink-500">Connected</div></div>
        <div><div class="text-2xl font-semibold">${rate}%</div><div class="text-xs text-ink-500">Connect rate</div></div>
      </div>
      ${total > 0 ? `
        <div class="flex-1 max-w-xs">
          <div class="h-2 rounded-full overflow-hidden bg-gray-100 flex">
            <div class="bg-emerald-500" style="width:${rate}%"></div>
            <div class="bg-rose-300" style="width:${100 - rate}%"></div>
          </div>
          <div class="flex justify-between text-[10px] text-ink-500 mt-1"><span>${connected} Connected</span><span>${missed} Missed</span></div>
        </div>` : ""}
    </div>
  `;

  const rows = total === 0
    ? `<div class="text-sm text-ink-500 italic px-5 py-4">No calls in this range.</div>`
    : calls.slice(0, 25).map(callRow).join("");

  return `
    <section>
      <h3 class="text-sm font-semibold mb-3 text-ink-700">Calls</h3>
      <div class="rounded-lg border border-gray-200 bg-white p-5">
        ${summaryRow}
        <div class="border-t border-gray-100 -mx-5"></div>
        <div class="divide-y divide-gray-100 -mx-5">${rows}</div>
      </div>
    </section>
  `;
}

function callRow(c: CallListItem): string {
  const status = c.connected
    ? `<div class="text-xs px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-100 w-24 text-center">Connected</div>`
    : `<div class="text-xs px-2 py-0.5 rounded-full bg-rose-50 text-rose-700 border border-rose-100 w-24 text-center">No answer</div>`;
  const sentDot = c.sentiment === "positive" || c.sentiment === "happy"
    ? `<span class="dot bg-emerald-500" title="Happy"></span>`
    : c.sentiment === "concerned" || c.sentiment === "frustrated"
    ? `<span class="dot bg-amber-500" title="Concerned"></span>`
    : `<span class="dot bg-gray-300" title="Neutral"></span>`;
  const desc = c.summary_text || (c.counterparty_name ? `${(c.direction || "").startsWith("out") ? "Outbound" : "Inbound"} · ${c.counterparty_name}` : "—");
  return `
    <div class="px-5 py-3 flex items-center gap-4 hover:bg-gray-50 cursor-pointer">
      <div class="text-xs text-ink-500 w-20 shrink-0">${esc(fmtTinyTime(c.started_at))}</div>
      ${status}
      <div class="flex-1 text-sm truncate">${esc(desc)}</div>
      <div class="text-xs text-ink-500">${esc(fmtDuration(c.duration_sec))}</div>
      ${sentDot}
    </div>
  `;
}

// ── Meetings section ────────────────────────────────────────────────────
function meetingsBlock(meetings: MeetingListItem[]): string {
  const total = meetings.length;
  const totalMin = meetings.reduce((s, m) => s + (m.duration_min || 0), 0);

  const summary = `
    <div class="flex items-baseline gap-6 mb-4">
      <div><div class="text-2xl font-semibold">${total}</div><div class="text-xs text-ink-500">Completed</div></div>
      <div><div class="text-2xl font-semibold">${Math.round(totalMin)}m</div><div class="text-xs text-ink-500">Total time</div></div>
    </div>
  `;

  const rows = total === 0
    ? `<div class="text-sm text-ink-500 italic px-5 py-4">No meetings in this range.</div>`
    : meetings.slice(0, 25).map(meetingRow).join("");

  return `
    <section class="mt-8">
      <h3 class="text-sm font-semibold mb-3 text-ink-700">Meetings done</h3>
      <div class="rounded-lg border border-gray-200 bg-white p-5">
        ${summary}
        <div class="border-t border-gray-100 -mx-5"></div>
        <div class="divide-y divide-gray-100 -mx-5">${rows}</div>
      </div>
    </section>
  `;
}

function meetingRow(m: MeetingListItem): string {
  const stack = avatarStack(m.attendee_initials || [], 3, "sm");
  return `
    <div class="px-5 py-3 flex items-center gap-4 hover:bg-gray-50 cursor-pointer">
      <div class="text-xs text-ink-500 w-24 shrink-0">${esc(fmtShortDay(m.date))}</div>
      <div class="flex-1 min-w-0">
        <div class="text-sm font-medium truncate">${esc(m.title)}</div>
        ${m.summary_short ? `<div class="text-xs text-ink-500 line-clamp-2">${esc(m.summary_short)}</div>` : ""}
      </div>
      ${stack}
      <span class="dot bg-gray-300"></span>
    </div>
  `;
}
