// Entry point — search, merchant load, tab routing.
import { Api } from "./api";
import { renderCalls } from "./components/calls";
import { renderMeetings } from "./components/meetings";
import { renderOverview } from "./components/overview";
import { renderTimeline } from "./components/timeline";
import { renderWhatsApp } from "./components/whatsapp";
import type { ShopProfile, ShopSummary } from "./types";
import { $, $$, esc, healthPill, toast } from "./utils";

let activeMerchant: ShopProfile | null = null;
let activeTab: TabKey = "overview";

type TabKey = "overview" | "meetings" | "calls" | "whatsapp" | "timeline";

const TAB_RENDERERS: Record<TabKey, (p: ShopProfile) => void | Promise<void>> = {
  overview:  renderOverview,
  meetings:  renderMeetings,
  calls:     renderCalls,
  whatsapp:  renderWhatsApp,
  timeline:  renderTimeline,
};

// ── search ───────────────────────────────────────────────────────────────
async function runSearch(q: string): Promise<void> {
  const list = $("#results");
  if (!list) return;
  if (!q.trim()) {
    list.innerHTML = "";
    return;
  }
  list.innerHTML = `<div class="text-xs text-gray-400 px-3 py-2">Searching…</div>`;
  const results = await Api.merchants(q, 30);
  if (!results || results.length === 0) {
    list.innerHTML = `<div class="text-xs text-gray-400 px-3 py-2">No matches.</div>`;
    return;
  }
  list.innerHTML = results.map((s) => `
    <button class="w-full text-left px-3 py-2 hover:bg-gray-50 border-b border-gray-100"
            data-shop-url="${esc(s.shop_url)}">
      <div class="text-sm font-medium">${esc(s.brand_name || s.shop_url)}</div>
      <div class="text-xs text-gray-500">${esc(s.shop_url)}</div>
    </button>
  `).join("");
  list.querySelectorAll<HTMLButtonElement>("button[data-shop-url]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const url = btn.dataset.shopUrl;
      if (url) loadMerchant(url);
    });
  });
}

// ── merchant load ────────────────────────────────────────────────────────
async function loadMerchant(shopUrl: string): Promise<void> {
  const profile = await Api.merchant(shopUrl);
  if (!profile) {
    toast("Could not load merchant");
    return;
  }
  activeMerchant = profile;
  renderHeader(profile);
  setTab(activeTab);
}

function renderHeader(p: ShopProfile): void {
  const header = $("#merchant-header");
  if (!header) return;
  header.innerHTML = `
    <div class="flex items-center gap-3">
      <div>
        <h1 class="text-lg font-semibold">${esc(p.brand_name || p.shop_url)}</h1>
        <div class="text-xs text-gray-500">${esc(p.shop_url)}</div>
      </div>
      ${healthPill(p.health_status)}
    </div>
  `;
  header.classList.remove("hidden");
  $("#tabs")?.classList.remove("hidden");
}

// ── tab routing ──────────────────────────────────────────────────────────
function setTab(tab: TabKey): void {
  activeTab = tab;
  $$("button[data-tab]").forEach((b) => {
    b.classList.toggle("tab-active", b.dataset.tab === tab);
  });
  if (activeMerchant) {
    void TAB_RENDERERS[tab](activeMerchant);
  }
}

// ── boot ─────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  const search = $<HTMLInputElement>("#q");
  if (search) {
    let t: number | undefined;
    search.addEventListener("input", () => {
      window.clearTimeout(t);
      t = window.setTimeout(() => runSearch(search.value), 200);
    });
  }
  $$("button[data-tab]").forEach((b) => {
    b.addEventListener("click", () => setTab(b.dataset.tab as TabKey));
  });
});
