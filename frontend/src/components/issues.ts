// "Issues" tab — Open / All / Resolved filter + ticket-style cards.
import { Api } from "../api";
import type { ShopProfile, IssueOut } from "../types";
import { $, emptyBlock, esc, fmtRelative, priorityPill, statusPill, toast } from "../utils";

type Filter = "open" | "all" | "resolved";
let activeFilter: Filter = "open";

export async function renderIssues(p: ShopProfile): Promise<void> {
  const body = $("#tab-body");
  if (!body) return;
  body.innerHTML = filterBar() + `<div class="text-xs text-slate-500 mt-3">Loading…</div>`;
  bind(p);

  const status = activeFilter === "all" ? undefined : activeFilter;
  const issues = (await Api.issues(p.shop_url, status)) || [];

  body.innerHTML = filterBar() + listOrEmpty(issues);
  bind(p);
}

function filterBar(): string {
  const btn = (key: Filter, label: string) => `
    <button data-filter="${key}"
            class="filter-pill ${activeFilter === key ? "filter-pill-active" : ""}">${label}</button>`;
  return `
    <div class="flex items-center justify-between mb-4">
      <div class="flex gap-2">${btn("open", "Open")}${btn("resolved", "Resolved")}${btn("all", "All")}</div>
      <button id="new-issue-btn" class="text-sm px-3 py-1.5 bg-slate-900 text-white rounded hover:bg-slate-800">+ Log new issue</button>
    </div>
  `;
}

function bind(p: ShopProfile): void {
  const root = $("#tab-body");
  if (!root) return;
  root.querySelectorAll<HTMLButtonElement>("button[data-filter]").forEach((btn) => {
    btn.addEventListener("click", () => {
      activeFilter = btn.dataset.filter as Filter;
      void renderIssues(p);
    });
  });
  $("#new-issue-btn")?.addEventListener("click", () => promptNewIssue(p));
}

function listOrEmpty(issues: IssueOut[]): string {
  if (issues.length === 0) return emptyBlock("No issues here.");
  return `<div class="space-y-2">${issues.map(card).join("")}</div>`;
}

function card(i: IssueOut): string {
  const ticketTag = i.jira_ticket_id ? `<span class="text-xs text-slate-500 font-mono">${esc(i.jira_ticket_id)}</span>` : `<span class="text-xs text-slate-400 font-mono">CS-${String(i.id).padStart(3, "0")}</span>`;
  return `
    <div class="card p-3">
      <div class="flex items-start justify-between gap-2">
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2 flex-wrap">
            ${ticketTag}
            ${priorityPill(i.priority)}
            ${statusPill(i.status)}
            <span class="text-sm font-medium truncate">${esc(i.title)}</span>
          </div>
          ${i.description ? `<div class="text-xs text-slate-600 mt-1 line-clamp-2">${esc(i.description)}</div>` : ""}
          <div class="text-xs text-slate-400 mt-2">
            opened ${esc(fmtRelative(i.opened_at))}
            ${i.source ? ` · source ${esc(i.source)}` : ""}
            ${i.owner ? ` · owner ${esc(i.owner)}` : ""}
          </div>
        </div>
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
