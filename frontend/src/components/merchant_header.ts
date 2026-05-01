// Header strip — matches the mockup exactly:
//   ← merchant name [Growth] [status pill]   PM avatar+name   Outreach: Open
import type { ShopProfile } from "../types";
import { esc, healthPill, initials } from "../utils";

export function renderMerchantHeader(p: ShopProfile): string {
  const am = p.account_manager
    ? `<div class="flex items-center gap-2 text-xs text-ink-500">
         <div class="w-6 h-6 rounded-full bg-indigo-100 text-indigo-700 grid place-items-center text-[10px] font-semibold">${esc(initials(p.account_manager))}</div>
         ${esc(p.account_manager)}
       </div>` : "";

  const outreach = p.outreach_status === "dnc"
    ? `<span class="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-rose-50 text-rose-700 border border-rose-100 text-xs font-medium">
         <span class="dot bg-rose-500"></span>Outreach: DNC
       </span>`
    : `<button id="outreach-btn" class="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-100 text-xs font-medium hover:bg-emerald-100">
         <span class="dot bg-emerald-500"></span>Outreach: Open
         <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 9l6 6 6-6"/></svg>
       </button>`;

  return `
    <div class="flex items-center gap-4">
      <button id="back-btn" class="text-ink-500 hover:text-ink-900 text-sm flex items-center gap-1">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M15 18l-6-6 6-6"/></svg>
      </button>
      <div>
        <div class="flex items-center gap-2">
          <h1 class="text-lg font-semibold tracking-tight">${esc(p.brand_name || p.shop_url)}</h1>
          <span class="text-xs px-2 py-0.5 rounded-md bg-gray-100 text-ink-700 font-medium">Growth</span>
          ${healthPill(p.health_status)}
        </div>
        <div class="text-xs text-ink-500 mt-0.5">${esc(p.shop_url)}</div>
      </div>
    </div>
    <div class="flex items-center gap-3">
      ${am}
      ${outreach}
    </div>
  `;
}
