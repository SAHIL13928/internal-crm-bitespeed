// "Issues" tab — segment-control filter + ticket-style cards (Jira-flavored ID).
// Layout matches the mockup pixel-for-pixel.
import { Api } from "../api";
import type { IssueOut, ShopProfile } from "../types";
import { $, esc, fmtRelative, priorityPill, statusPill, toast } from "../utils";

type Filter = "open" | "all" | "resolved";
let activeFilter: Filter = "open";

export async function renderIssues(p: ShopProfile): Promise<void> {
  const body = $("#tab-body");
  if (!body) return;
  body.innerHTML = head() + `<div class="rounded-lg border border-gray-200 bg-white p-6 text-sm text-ink-500 italic">Loading…</div>`;
  bind(p);

  const status = activeFilter === "all" ? undefined : activeFilter;
  const issues = (await Api.issues(p.shop_url, status)) || [];

  body.innerHTML = head() + body_(issues);
  bind(p);
}

function head(): string {
  const seg = (key: Filter, label: string) => `
    <button data-filter="${key}"
            class="px-3 py-1 text-xs rounded-md ${activeFilter === key ? "bg-white text-ink-900 shadow-sm font-medium" : "text-ink-500"}">${esc(label)}</button>`;
  return `
    <div class="flex items-center justify-between mb-4">
      <div class="flex items-center gap-3">
        <h2 class="text-base font-semibold">Issues</h2>
        <div class="flex items-center gap-1 p-1 bg-gray-100 rounded-lg">
          ${seg("open", "Open")}${seg("all", "All")}${seg("resolved", "Resolved")}
        </div>
      </div>
      <button id="new-issue-btn"
              class="text-xs px-3 py-1.5 rounded-lg bg-ink-900 text-white hover:bg-ink-700 inline-flex items-center gap-1.5">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M5 12h14"/></svg>
        Log new issue
      </button>
    </div>
  `;
}

function bind(p: ShopProfile) {
  document.querySelectorAll<HTMLButtonElement>("button[data-filter]").forEach((btn) => {
    btn.addEventListener("click", () => {
      activeFilter = btn.dataset.filter as Filter;
      void renderIssues(p);
    });
  });
  $("#new-issue-btn")?.addEventListener("click", () => promptNewIssue(p));
}

function body_(issues: IssueOut[]): string {
  if (issues.length === 0) {
    return `<div class="rounded-lg border border-gray-200 bg-white p-6 text-sm text-ink-500 italic">No issues here.</div>`;
  }
  return `<div class="rounded-lg border border-gray-200 bg-white divide-y divide-gray-100">${issues.map(card).join("")}</div>`;
}

function card(i: IssueOut): string {
  const ticket = i.jira_ticket_id ? esc(i.jira_ticket_id) : `CS-${String(i.id).padStart(3, "0")}`;
  const status = statusPill(i.status === "in_progress" ? "in_progress" : i.status === "resolved" ? "resolved" : "open");
  const opened = `${i.opened_at ? fmtRelative(i.opened_at) : "—"}`;

  return `
    <div class="p-4 hover:bg-gray-50 cursor-pointer">
      <div class="flex items-start justify-between gap-4">
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2 flex-wrap">
            <span class="text-[10px] font-mono px-1.5 py-0.5 rounded bg-[#0052CC]/10 text-[#0052CC] font-semibold">${ticket}</span>
            ${priorityPill(i.priority)}
            <div class="text-sm font-medium truncate">${esc(i.title)}</div>
          </div>
          <div class="text-xs text-ink-500 mt-1">
            Opened ${esc(opened)}
            ${i.source ? ` · Source: ${esc(i.source)}` : ""}
            ${i.owner ? ` · Owner: ${esc(i.owner)}` : ""}
          </div>
          ${i.description ? `<div class="text-sm text-ink-700 mt-2 line-clamp-2">${esc(i.description)}</div>` : ""}
        </div>
        <div class="shrink-0">${status}</div>
      </div>
    </div>
  `;
}

async function promptNewIssue(p: ShopProfile): Promise<void> {
  const title = window.prompt("Issue title?");
  if (!title) return;
  const description = window.prompt("Description (optional)?") || undefined;
  const priority = (window.prompt("Priority? high / med / low", "med") || "med").toLowerCase() as "high" | "med" | "low";
  const ok = await Api.createIssue(p.shop_url, { title, description, priority });
  if (ok) { toast("Issue logged"); void renderIssues(p); }
  else    { toast("Failed to log issue"); }
}
