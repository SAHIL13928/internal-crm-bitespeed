// Overview tab — KPI strip + contacts summary.
import { Api } from "../api";
import type { ShopProfile } from "../types";
import { $, esc, fmtAbsolute, fmtRelative, healthPill, initials } from "../utils";

export function renderOverview(p: ShopProfile): void {
  const body = $("#tab-body");
  if (!body) return;
  const k = p.kpi;
  const kpiTile = (label: string, value: string | number, sub?: string) => `
    <div class="bg-white border border-gray-200 rounded p-3">
      <div class="text-xs text-gray-500">${esc(label)}</div>
      <div class="text-2xl font-semibold mt-1">${esc(String(value))}</div>
      ${sub ? `<div class="text-xs text-gray-400 mt-1">${esc(sub)}</div>` : ""}
    </div>`;

  const lastContact = k.last_contact_at
    ? `${fmtRelative(k.last_contact_at)} · ${k.last_contact_kind ?? ""}`
    : "—";

  const connectRate = k.calls_7d_connect_rate_pct === null
    ? "—" : `${k.calls_7d_connect_rate_pct}%`;

  const kpis = `
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
      ${kpiTile("Upcoming meetings", k.upcoming_meetings)}
      ${kpiTile("Meetings (7d)", k.meetings_7d, k.meetings_7d_total_minutes ? `${Math.round(k.meetings_7d_total_minutes)} min total` : undefined)}
      ${kpiTile("Calls (7d)", `${k.calls_7d_connected}/${k.calls_7d_attempted}`, `${connectRate} connect rate`)}
      ${kpiTile("Open issues", k.open_issues)}
      ${kpiTile("WhatsApp groups", k.whatsapp_groups)}
      ${kpiTile("Last contact", lastContact)}
      ${kpiTile("Health", "")}<!-- placeholder for health pill, drawn separately -->
    </div>`;

  const contactsList = (p.contacts || []).slice(0, 12).map((c) => `
    <div class="flex items-center gap-2 py-1 text-sm">
      <span class="inline-flex items-center justify-center w-6 h-6 rounded-full bg-gray-100 text-xs">${esc(initials(c.name))}</span>
      <span class="font-medium">${esc(c.name || "(no name)")}</span>
      ${c.role ? `<span class="text-xs text-gray-500">${esc(c.role)}</span>` : ""}
      ${c.is_internal ? `<span class="text-xs text-blue-600">[BS]</span>` : ""}
      ${c.phone ? `<span class="text-xs text-gray-500">📞 ${esc(c.phone)}</span>` : ""}
      ${c.email ? `<span class="text-xs text-gray-500">${esc(c.email)}</span>` : ""}
    </div>`).join("");

  body.innerHTML = `
    ${kpis}
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <div class="bg-white border border-gray-200 rounded p-4">
        <div class="flex justify-between items-baseline mb-2">
          <h3 class="font-semibold">Contacts</h3>
          <span class="text-xs text-gray-500">${(p.contacts || []).length}</span>
        </div>
        ${contactsList || `<div class="text-xs text-gray-400 italic">No contacts on file</div>`}
      </div>
      <div class="bg-white border border-gray-200 rounded p-4">
        <div class="flex justify-between items-baseline mb-2">
          <h3 class="font-semibold">Status</h3>
          ${healthPill(p.health_status)}
        </div>
        <div class="text-sm text-gray-600 space-y-1">
          <div>Outreach: <span class="font-medium">${esc(p.outreach_status)}</span></div>
          ${p.account_manager ? `<div>AM: <span class="font-medium">${esc(p.account_manager)}</span></div>` : ""}
          ${p.dnc_reason ? `<div class="text-rose-600">DNC: ${esc(p.dnc_reason)}</div>` : ""}
        </div>
      </div>
    </div>
  `;
}
