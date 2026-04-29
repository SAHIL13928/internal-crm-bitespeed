// KPI strip — 6 tiles always visible at top, matches the reference layout.
import type { ShopProfile } from "../types";
import { esc, fmtRelative } from "../utils";

export function renderKpiStrip(p: ShopProfile): string {
  const k = p.kpi;
  const tile = (label: string, value: string | number, sub?: string) => `
    <div class="card p-3">
      <div class="text-[11px] uppercase tracking-wide text-slate-400">${esc(label)}</div>
      <div class="text-xl font-semibold mt-1">${esc(String(value))}</div>
      ${sub ? `<div class="text-[11px] text-slate-500 mt-0.5">${esc(sub)}</div>` : ""}
    </div>`;

  const connectRate = k.calls_7d_connect_rate_pct === null ? "—" : `${k.calls_7d_connect_rate_pct}%`;
  const lastContact = k.last_contact_at
    ? `${fmtRelative(k.last_contact_at)}${k.last_contact_kind ? ` · ${k.last_contact_kind}` : ""}`
    : "—";

  return `
    ${tile("Upcoming touches", k.upcoming_meetings, "next 7d")}
    ${tile("Calls · 7d", `${k.calls_7d_connected} / ${k.calls_7d_attempted}`, `${connectRate} connect`)}
    ${tile("Meetings · 7d", k.meetings_7d, k.meetings_7d_total_minutes ? `${Math.round(k.meetings_7d_total_minutes)} min total` : "")}
    ${tile("WhatsApp groups", k.whatsapp_groups)}
    ${tile("Open issues", k.open_issues)}
    ${tile("Last contact", lastContact)}
  `;
}
