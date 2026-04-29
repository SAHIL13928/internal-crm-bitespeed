// "Activity" tab — calls + meetings within a selected date range.
// Filter pills (Today / Yesterday / 7d / 30d / month / all) match the reference.
import { Api } from "../api";
import type { ShopProfile, CallListItem, MeetingListItem } from "../types";
import { $, avatarStack, connectedDot, directionPill, esc, fmtDayLabel, fmtRelative } from "../utils";
import { DEFAULT_RANGE, rangeFilterFromKey, renderRangeFilter } from "./range_filter";
import type { RangeKey } from "../utils";

let activeRange: RangeKey = DEFAULT_RANGE;

export async function renderActivity(p: ShopProfile): Promise<void> {
  const body = $("#tab-body");
  if (!body) return;
  body.innerHTML = renderRangeFilter(activeRange) + `<div class="text-xs text-slate-500 px-1">Loading…</div>`;
  bindFilter(p);

  const f = rangeFilterFromKey(activeRange);
  const [calls, meetings] = await Promise.all([
    Api.calls(p.shop_url, { ...f, limit: 100 }),
    Api.meetings(p.shop_url, { ...f, limit: 100 }),
  ]);

  body.innerHTML = renderRangeFilter(activeRange)
    + renderCallsBlock(calls || [])
    + renderMeetingsBlock(meetings || []);
  bindFilter(p);
}

function bindFilter(p: ShopProfile) {
  const root = $("#tab-body");
  if (!root) return;
  root.querySelectorAll<HTMLButtonElement>("button[data-range]").forEach((btn) => {
    btn.addEventListener("click", () => {
      activeRange = btn.dataset.range as RangeKey;
      void renderActivity(p);
    });
  });
}

// ── Calls block ──────────────────────────────────────────────────────────
function renderCallsBlock(calls: CallListItem[]): string {
  const total = calls.length;
  const connected = calls.filter((c) => c.connected).length;
  const rate = total > 0 ? Math.round((100 * connected) / total) : null;

  if (total === 0) {
    return `
      <section class="mb-6">
        <h3 class="text-sm font-semibold mb-2">Calls</h3>
        <div class="card p-4 text-sm text-slate-500 italic">No calls in this range.</div>
      </section>`;
  }

  const fmtDur = (s: number | null) => s == null ? "—"
    : s < 60 ? `${Math.round(s)}s`
    : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;

  return `
    <section class="mb-6">
      <div class="flex items-baseline justify-between mb-3">
        <h3 class="text-sm font-semibold">Calls</h3>
        <div class="text-xs text-slate-500">${total} total · ${connected} connected${rate !== null ? ` · ${rate}%` : ""}</div>
      </div>
      <div class="space-y-2">
        ${calls.slice(0, 25).map((c) => `
          <div class="card p-3 text-sm">
            <div class="flex items-center justify-between gap-2">
              <div class="flex items-center gap-2 min-w-0">
                ${connectedDot(c.connected)}
                ${directionPill(c.direction)}
                <span class="font-medium truncate">${esc(c.counterparty_name || c.counterparty_phone || "—")}</span>
                ${c.counterparty_role ? `<span class="text-xs text-slate-500">${esc(c.counterparty_role)}</span>` : ""}
              </div>
              <div class="flex items-center gap-3 text-xs text-slate-500 whitespace-nowrap">
                <span>${esc(fmtDur(c.duration_sec))}</span>
                <span>${esc(fmtRelative(c.started_at))}</span>
              </div>
            </div>
            ${c.summary_text ? `<div class="text-xs text-slate-600 mt-2 line-clamp-2">${esc(c.summary_text)}</div>` : ""}
          </div>
        `).join("")}
      </div>
    </section>`;
}

// ── Meetings block ──────────────────────────────────────────────────────
function renderMeetingsBlock(meetings: MeetingListItem[]): string {
  const total = meetings.length;
  const totalMin = meetings.reduce((s, m) => s + (m.duration_min || 0), 0);

  if (total === 0) {
    return `
      <section>
        <h3 class="text-sm font-semibold mb-2">Meetings</h3>
        <div class="card p-4 text-sm text-slate-500 italic">No meetings in this range.</div>
      </section>`;
  }

  return `
    <section>
      <div class="flex items-baseline justify-between mb-3">
        <h3 class="text-sm font-semibold">Meetings</h3>
        <div class="text-xs text-slate-500">${total} done · ${Math.round(totalMin)} min total</div>
      </div>
      <div class="space-y-2">
        ${meetings.slice(0, 25).map((m) => `
          <div class="card p-3">
            <div class="flex items-start justify-between gap-3">
              <div class="flex-1 min-w-0">
                <div class="text-xs text-slate-500 mb-1">${esc(fmtDayLabel(m.date))}</div>
                <div class="text-sm font-medium truncate">${esc(m.title)}</div>
                ${m.summary_short ? `<div class="text-xs text-slate-600 mt-1 line-clamp-2">${esc(m.summary_short)}</div>` : ""}
              </div>
              <div class="flex items-center pr-1">${avatarStack(m.attendee_initials || [])}</div>
            </div>
          </div>
        `).join("")}
      </div>
    </section>`;
}
