// Entry point — two-screen flow (search → profile), tab routing.
// Mirrors the mockup's behavior: typing in the search input shows a
// floating result list under it; clicking a result opens the full
// profile screen.
import { Api } from "./api";
import { renderCallsMeetings } from "./components/calls_meetings";
import { renderIssues } from "./components/issues";
import { renderKpiStrip } from "./components/kpi_strip";
import { renderMerchantHeader } from "./components/merchant_header";
import { renderNotes } from "./components/notes";
import { renderUpcoming } from "./components/upcoming";
import { renderWhatsApp } from "./components/whatsapp";
import type { ShopProfile, ShopSummary } from "./types";
import { $, $$, esc, fmtRelative, healthPill, toast } from "./utils";

let activeMerchant: ShopProfile | null = null;
let activeTab: TabKey = "upcoming";

type TabKey = "upcoming" | "calls" | "whatsapp" | "issues" | "notes";

const TAB_RENDERERS: Record<TabKey, (p: ShopProfile) => void | Promise<void>> = {
  upcoming: renderUpcoming,
  calls:    renderCallsMeetings,
  whatsapp: renderWhatsApp,
  issues:   renderIssues,
  notes:    renderNotes,
};

// ── Search screen ───────────────────────────────────────────────────────
function showSearchScreen(): void {
  $("#screen-search")?.classList.remove("hidden");
  $("#screen-profile")?.classList.add("hidden");
  activeMerchant = null;
}

function showProfileScreen(): void {
  $("#screen-search")?.classList.add("hidden");
  $("#screen-profile")?.classList.remove("hidden");
}

async function refreshSearchStats(): Promise<void> {
  const stats = $("#search-stats");
  if (!stats) return;
  const h = await Api.health();
  if (!h) { stats.textContent = "API unreachable"; return; }
  stats.textContent = `${h.shops.toLocaleString()} merchants · ${h.calls.toLocaleString()} calls · ${h.meetings.toLocaleString()} meetings`;
}

// Read-only check on the calendar wiring — shown to AMs on the search
// screen so they don't have to memorize /auth/google/connect.
//   • Endpoint 503 (Google client not configured on the server) → hide
//     the banner; nothing the AM can do about it themselves.
//   • Empty list → "Connect your calendar" CTA.
//   • One+ active connection → "Calendar synced …" + "manage" link.
//   • At least one failing/revoked → "reconnect" CTA (Google only
//     re-issues a refresh token on a fresh consent, so reconnect is
//     the right verb, not retry).
async function refreshCalendarBanner(): Promise<void> {
  const el = $("#calendar-banner");
  if (!el) return;
  // Server-side env check first — if Google client creds aren't on the
  // box, clicking "Connect" would just 503. Hide entirely in that case;
  // the "configure GCP" task is on the operator, not the AM.
  const h = await Api.health();
  if (!h?.google_calendar?.configured) return;

  const conns = await Api.calendarConnections();
  if (conns === null) return;

  const active = conns.filter((c) => c.status === "active");
  const failing = conns.filter((c) => c.status === "failing" || c.status === "revoked");

  if (active.length === 0 && failing.length === 0) {
    el.innerHTML = `
      <a href="/auth/google/connect"
         class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full
                border border-indigo-200 text-indigo-700 hover:bg-indigo-50">
        <span class="dot bg-indigo-500"></span>
        Connect your Google Calendar
      </a>`;
  } else if (failing.length > 0 && active.length === 0) {
    el.innerHTML = `
      <a href="/auth/google/connect"
         class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full
                border border-amber-200 text-amber-700 hover:bg-amber-50">
        <span class="dot bg-amber-500"></span>
        Calendar connection needs attention — reconnect
      </a>`;
  } else {
    const last = active
      .map((c) => c.last_synced_at)
      .filter((s): s is string => Boolean(s))
      .sort()
      .pop();
    const lastTxt = last ? `synced ${fmtRelative(last)}` : "not yet synced";
    el.innerHTML = `
      <span class="text-ink-500">
        <span class="dot bg-emerald-500"></span>
        Calendar: ${active.length} account${active.length > 1 ? "s" : ""} · ${lastTxt}
      </span>
      <a href="/auth/google/connections" class="ml-2 text-indigo-600 underline">manage</a>`;
  }
  el.classList.remove("hidden");
}

async function runSearch(q: string): Promise<void> {
  const list = $("#search-results");
  if (!list) return;
  if (!q.trim()) { list.classList.add("hidden"); list.innerHTML = ""; return; }
  list.classList.remove("hidden");
  list.innerHTML = `<div class="text-xs text-ink-300 px-4 py-3">Searching…</div>`;
  const results = await Api.merchants(q, 8);
  if (!results || results.length === 0) {
    list.innerHTML = `<div class="text-xs text-ink-300 px-4 py-3">No matches.</div>`;
    return;
  }
  list.innerHTML = results.map(resultRow).join("");
  list.querySelectorAll<HTMLButtonElement>("button[data-shop-url]").forEach((btn) => {
    btn.addEventListener("click", () => loadMerchant(btn.dataset.shopUrl!));
  });
}

function resultRow(s: ShopSummary): string {
  return `
    <button data-shop-url="${esc(s.shop_url)}"
            class="w-full text-left px-4 py-3 hover:bg-gray-50 flex items-center justify-between border-t border-gray-100 first:border-t-0">
      <div class="min-w-0">
        <div class="text-sm font-medium truncate">${esc(s.brand_name || s.shop_url)}</div>
        <div class="text-xs text-ink-500 truncate">${esc(s.shop_url)}</div>
      </div>
      ${healthPill(s.health_status)}
    </button>
  `;
}

// ── Merchant load ───────────────────────────────────────────────────────
async function loadMerchant(shopUrl: string): Promise<void> {
  const profile = await Api.merchant(shopUrl);
  if (!profile) { toast("Could not load merchant"); return; }
  activeMerchant = profile;

  const header = $("#profile-header");
  if (header) header.innerHTML = renderMerchantHeader(profile);
  $("#back-btn")?.addEventListener("click", showSearchScreen);

  const kpi = $("#kpi-strip");
  if (kpi) kpi.innerHTML = renderKpiStrip(profile);

  showProfileScreen();
  setTab(activeTab);
}

// ── Tab routing ─────────────────────────────────────────────────────────
function setTab(tab: TabKey): void {
  activeTab = tab;
  $$("button[data-tab]").forEach((b) => {
    b.setAttribute("data-active", b.dataset.tab === tab ? "true" : "false");
  });
  if (activeMerchant) void TAB_RENDERERS[tab](activeMerchant);
}

// ── Boot ────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  void refreshSearchStats();
  void refreshCalendarBanner();
  // ?google_connected=1 → toast + refresh banner so the AM sees their
  // freshly-connected account immediately.
  if (new URLSearchParams(location.search).get("google_connected") === "1") {
    toast("Calendar connected");
    history.replaceState(null, "", location.pathname);
    setTimeout(() => void refreshCalendarBanner(), 250);
  }

  const search = $<HTMLInputElement>("#search-input");
  if (search) {
    let t: number | undefined;
    search.addEventListener("input", () => {
      window.clearTimeout(t);
      t = window.setTimeout(() => runSearch(search.value), 200);
    });
    search.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        const first = $<HTMLButtonElement>("#search-results button[data-shop-url]");
        first?.click();
      } else if (e.key === "Escape") {
        search.value = ""; runSearch("");
      }
    });
  }

  $$("button[data-tab]").forEach((b) => {
    b.addEventListener("click", () => setTab(b.dataset.tab as TabKey));
  });
});
