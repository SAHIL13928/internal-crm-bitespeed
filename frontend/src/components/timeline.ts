// Timeline tab — unified meetings + calls + notes + issues.
import { Api } from "../api";
import type { ShopProfile, TimelineItem } from "../types";
import { $, emptyBlock, esc, fmtAbsolute } from "../utils";

const TYPE_BADGE: Record<TimelineItem["type"], string> = {
  meeting: "bg-indigo-50 text-indigo-700",
  call:    "bg-blue-50 text-blue-700",
  note:    "bg-amber-50 text-amber-700",
  issue:   "bg-rose-50 text-rose-700",
};

export async function renderTimeline(p: ShopProfile): Promise<void> {
  const body = $("#tab-body");
  if (!body) return;
  body.innerHTML = `<div class="text-xs text-gray-500">Loading…</div>`;

  const items = await Api.timeline(p.shop_url, 200);
  if (!items || items.length === 0) {
    body.innerHTML = emptyBlock("Nothing on the timeline yet.");
    return;
  }

  body.innerHTML = `
    <div class="space-y-2">
      ${items.map((it) => `
        <div class="bg-white border border-gray-200 rounded p-3 text-sm">
          <div class="flex justify-between items-baseline">
            <div class="flex items-center gap-2">
              <span class="pill ${TYPE_BADGE[it.type]}">${esc(it.type)}</span>
              <span class="font-medium">${esc(it.title)}</span>
            </div>
            <div class="text-xs text-gray-500">${esc(fmtAbsolute(it.timestamp))}</div>
          </div>
          ${it.summary ? `<div class="text-xs text-gray-600 mt-1 line-clamp-3">${esc(it.summary)}</div>` : ""}
        </div>
      `).join("")}
    </div>
  `;
}
