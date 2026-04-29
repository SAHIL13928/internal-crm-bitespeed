// Meetings tab — list of past + upcoming meetings.
import { Api } from "../api";
import type { ShopProfile } from "../types";
import { $, emptyBlock, esc, fmtAbsolute } from "../utils";

export async function renderMeetings(p: ShopProfile): Promise<void> {
  const body = $("#tab-body");
  if (!body) return;
  body.innerHTML = `<div class="text-xs text-gray-500">Loading…</div>`;

  const rows = await Api.meetings(p.shop_url, 100);
  if (!rows || rows.length === 0) {
    body.innerHTML = emptyBlock("No meetings linked to this merchant.");
    return;
  }

  const now = new Date();
  const upcoming = rows.filter((m) => m.date && new Date(m.date) > now);
  const past = rows.filter((m) => !m.date || new Date(m.date) <= now);

  const card = (m: typeof rows[number]) => `
    <div class="bg-white border border-gray-200 rounded p-3 text-sm">
      <div class="flex justify-between items-baseline">
        <div class="font-medium">${esc(m.title)}</div>
        <div class="text-xs text-gray-500">${esc(fmtAbsolute(m.date))}</div>
      </div>
      ${m.summary_short ? `<div class="text-xs text-gray-600 mt-1">${esc(m.summary_short)}</div>` : ""}
      <div class="text-xs text-gray-400 mt-1">
        ${m.attendee_count ? `${m.attendee_count} attendee${m.attendee_count === 1 ? "" : "s"}` : ""}
        ${m.duration_min ? ` · ${Math.round(m.duration_min)} min` : ""}
        ${m.mapping_source ? ` · via ${esc(m.mapping_source)}` : ""}
      </div>
    </div>`;

  body.innerHTML = `
    ${upcoming.length ? `
      <h3 class="text-xs font-semibold text-gray-500 mb-2 uppercase tracking-wide">Upcoming (${upcoming.length})</h3>
      <div class="space-y-2 mb-5">${upcoming.map(card).join("")}</div>
    ` : ""}
    ${past.length ? `
      <h3 class="text-xs font-semibold text-gray-500 mb-2 uppercase tracking-wide">Past (${past.length})</h3>
      <div class="space-y-2">${past.map(card).join("")}</div>
    ` : ""}
  `;
}
