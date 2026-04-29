// Entry point — search, merchant load, tab routing.
import { Api } from "./api";
import { renderActivity } from "./components/activity";
import { renderIssues } from "./components/issues";
import { renderKpiStrip } from "./components/kpi_strip";
import { renderMerchantHeader } from "./components/merchant_header";
import { renderNotes } from "./components/notes";
import { renderUpcoming } from "./components/upcoming";
import { renderWhatsApp } from "./components/whatsapp";
import type { ShopProfile, ShopSummary } from "./types";
import { $, $$, esc, healthPill, toast } from "./utils";

let activeMerchant: ShopProfile | null = null;
let activeTab: TabKey = "upcoming";

type TabKey = "upcoming" | "activity" | "whatsapp" | "issues" | "notes";

const TAB_RENDERERS: Record<TabKey, (p: ShopProfile) => void | Promise<void>> = {
  upcoming: renderUpcoming,
  activity: renderActivity,
  whatsapp: renderWhatsApp,
  issues:   renderIssues,
  notes:    renderNotes,
};

// ── Top-bar health-tag (shows DB stats) ─────────────────────────────────
async function refreshHealthTag(): Promise<void> {
  const h = await Api.health();
  const tag = $("#health-tag");
  if (!tag) return;
  if (!h) { tag.textContent = "API unreachable"; return; }
  tag.textContent = `${h.shops.toLocaleString()} merchants · ${h.calls.toLocaleString()} calls · ${h.meetings.toLocaleString()} meetings`;
}

// ── Search ──────────────────────────────────────────────────────────────
async function runSearch(q: string): Promise<void> {
  const list = $("#results");
  if (!list) return;
  if (!q.trim()) { list.innerHTML = ""; return; }
  list.innerHTML = `<div class="text-xs text-slate-400 px-3 py-2">Searching…</div>`;
  const results = await Api.merchants(q, 30);
  if (!results || results.length === 0) {
    list.innerHTML = `<div class="text-xs text-slate-400 px-3 py-2">No matches.</div>`;
    return;
  }
  list.innerHTML = results.map(renderResultCard).join("");
  list.querySelectorAll<HTMLButtonElement>("button[data-shop-url]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const url = btn.dataset.shopUrl;
      if (url) void loadMerchant(url);
    });
  });
}

function renderResultCard(s: ShopSummary): string {
  return `
    <button class="w-full text-left px-4 py-3 hover:bg-slate-50 border-b border-slate-100 transition-colors"
            data-shop-url="${esc(s.shop_url)}">
      <div class="flex items-center justify-between gap-2">
        <div class="text-sm font-medium truncate">${esc(s.brand_name || s.shop_url)}</div>
        ${healthPill(s.health_status)}
      </div>
      <div class="text-xs text-slate-500 mt-0.5 truncate">${esc(s.shop_url)}</div>
    </button>`;
}

// ── Merchant load ───────────────────────────────────────────────────────
async function loadMerchant(shopUrl: string): Promise<void> {
  const profile = await Api.merchant(shopUrl);
  if (!profile) { toast("Could not load merchant"); return; }
  activeMerchant = profile;

  const header = $("#merchant-header");
  if (header) header.innerHTML = renderMerchantHeader(profile);

  const kpi = $("#kpi-strip");
  if (kpi) kpi.innerHTML = renderKpiStrip(profile);

  $("#empty-state")?.classList.add("hidden");
  $("#merchant-shell")?.classList.remove("hidden");

  setTab(activeTab);
}

// ── Tab routing ─────────────────────────────────────────────────────────
function setTab(tab: TabKey): void {
  activeTab = tab;
  $$("button[data-tab]").forEach((b) => {
    b.classList.toggle("tab-active", b.dataset.tab === tab);
  });
  if (activeMerchant) void TAB_RENDERERS[tab](activeMerchant);
}

// ── Boot ────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  void refreshHealthTag();

  const search = $<HTMLInputElement>("#q");
  if (search) {
    let t: number | undefined;
    search.addEventListener("input", () => {
      window.clearTimeout(t);
      t = window.setTimeout(() => runSearch(search.value), 200);
    });
    // Enter on result list opens first match
    search.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        const first = $("#results button[data-shop-url]") as HTMLButtonElement | null;
        first?.click();
      }
      if (e.key === "Escape") { search.value = ""; void runSearch(""); }
    });
  }
  $$("button[data-tab]").forEach((b) => {
    b.addEventListener("click", () => setTab(b.dataset.tab as TabKey));
  });
});
