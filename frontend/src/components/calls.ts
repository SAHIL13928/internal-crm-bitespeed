// Calls tab — list of FreJun calls with AI insights.
import { Api } from "../api";
import type { ShopProfile } from "../types";
import { $, connectedDot, directionPill, emptyBlock, esc, fmtAbsolute } from "../utils";

export async function renderCalls(p: ShopProfile): Promise<void> {
  const body = $("#tab-body");
  if (!body) return;
  body.innerHTML = `<div class="text-xs text-gray-500">Loading…</div>`;

  const rows = await Api.calls(p.shop_url, 100);
  if (!rows || rows.length === 0) {
    body.innerHTML = emptyBlock("No calls logged for this merchant yet.");
    return;
  }

  const fmtDur = (s: number | null) => {
    if (s === null || s === undefined) return "—";
    if (s < 60) return `${Math.round(s)}s`;
    return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
  };

  body.innerHTML = `
    <div class="space-y-2">
      ${rows.map((c) => `
        <div class="bg-white border border-gray-200 rounded p-3 text-sm">
          <div class="flex justify-between items-baseline">
            <div class="flex items-center gap-2">
              ${connectedDot(c.connected)}
              ${directionPill(c.direction)}
              <span class="font-medium">${esc(c.counterparty_name || c.counterparty_phone || "")}</span>
              ${c.counterparty_role ? `<span class="text-xs text-gray-500">${esc(c.counterparty_role)}</span>` : ""}
            </div>
            <div class="text-xs text-gray-500">${esc(fmtAbsolute(c.started_at))}</div>
          </div>
          <div class="text-xs text-gray-500 mt-1">
            duration ${esc(fmtDur(c.duration_sec))}
            ${c.agent_name ? ` · agent ${esc(c.agent_name)}` : ""}
            ${c.sentiment ? ` · sentiment ${esc(c.sentiment)}` : ""}
          </div>
          ${c.summary_text ? `<div class="text-xs text-gray-600 mt-2 line-clamp-3">${esc(c.summary_text)}</div>` : ""}
        </div>
      `).join("")}
    </div>
  `;
}
