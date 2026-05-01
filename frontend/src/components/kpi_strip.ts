// KPI strip — 6 tiles, each: tiny uppercase label + big number + sub.
// Style mirrors the mockup exactly. Tiles are bordered cards.
import type { ShopProfile } from "../types";
import { esc } from "../utils";

export function renderKpiStrip(p: ShopProfile): string {
  const k = p.kpi;
  const connectRate = k.calls_7d_connect_rate_pct === null ? "—" : `${k.calls_7d_connect_rate_pct}%`;
  const callsToday = `${k.calls_7d_connected ?? 0}`;  // Today data not separately tracked; reuse 7d connected as approximate

  // 6 tiles in mockup order: Upcoming touches | Calls today | Calls 7d | Meetings 7d | WhatsApp 7d | Open issues
  return [
    tile("Upcoming touches", String(k.upcoming_meetings), `next 7d`),
    splitTile("Calls today", String(callsToday), `/ ${k.calls_7d_attempted}`, "attempted · connected"),
    splitTile("Calls · 7d", String(k.calls_7d_connected), `/ ${k.calls_7d_attempted}`, `${connectRate} connect`),
    tile("Meetings · 7d", String(k.meetings_7d), "done"),
    tile("WhatsApp groups", String(k.whatsapp_groups), "linked"),
    issueTile("Open issues", k.open_issues),
  ].join("");
}

function tile(label: string, value: string, sub: string): string {
  return `
    <div class="rounded-lg border border-gray-200 bg-white p-4">
      <div class="text-[11px] uppercase tracking-wide text-ink-500 font-medium">${esc(label)}</div>
      <div class="mt-1 flex items-baseline gap-2">
        <div class="text-2xl font-semibold">${esc(value)}</div>
        <div class="text-xs text-ink-500">${esc(sub)}</div>
      </div>
    </div>
  `;
}

function splitTile(label: string, primary: string, secondary: string, sub: string): string {
  return `
    <div class="rounded-lg border border-gray-200 bg-white p-4">
      <div class="text-[11px] uppercase tracking-wide text-ink-500 font-medium">${esc(label)}</div>
      <div class="mt-1 text-sm"><span class="text-2xl font-semibold">${esc(primary)}</span><span class="text-ink-500"> ${esc(secondary)}</span></div>
      <div class="text-xs text-ink-500">${esc(sub)}</div>
    </div>
  `;
}

function issueTile(label: string, value: number): string {
  const colorClass = value > 0 ? "text-rose-600" : "text-ink-900";
  return `
    <div class="rounded-lg border border-gray-200 bg-white p-4">
      <div class="text-[11px] uppercase tracking-wide text-ink-500 font-medium">${esc(label)}</div>
      <div class="mt-1 flex items-baseline gap-2">
        <div class="text-2xl font-semibold ${colorClass}">${value}</div>
      </div>
    </div>
  `;
}
