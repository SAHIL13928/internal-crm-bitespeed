// Header strip — merchant name, health badge, AM, outreach status.
import type { ShopProfile } from "../types";
import { esc, healthPill } from "../utils";

export function renderMerchantHeader(p: ShopProfile): string {
  const am = p.account_manager ? `<span class="text-xs text-slate-500">PM <span class="font-medium text-slate-700">${esc(p.account_manager)}</span></span>` : "";
  const outreach = p.outreach_status === "dnc"
    ? `<span class="pill pill-dnc">DNC</span>`
    : `<span class="text-xs text-slate-500">Outreach <span class="font-medium text-slate-700">Open</span></span>`;
  return `
    <div class="card p-4 flex items-start justify-between">
      <div class="flex items-start gap-3">
        <div>
          <div class="flex items-center gap-2">
            <h2 class="text-lg font-semibold leading-tight">${esc(p.brand_name || p.shop_url)}</h2>
            ${healthPill(p.health_status)}
          </div>
          <div class="text-xs text-slate-500 mt-1">${esc(p.shop_url)}</div>
        </div>
      </div>
      <div class="flex items-center gap-3">
        ${am}
        ${outreach}
      </div>
    </div>
  `;
}
