// Pure helpers — no DOM mutations live here.

export const $  = <T extends HTMLElement = HTMLElement>(sel: string): T | null => document.querySelector<T>(sel);
export const $$ = <T extends HTMLElement = HTMLElement>(sel: string): T[] => Array.from(document.querySelectorAll<T>(sel));

export function esc(s: string | null | undefined): string {
  if (s === null || s === undefined) return "";
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
                  .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

export function fmtAbsolute(iso: string | null | undefined, opts: { withTime?: boolean } = { withTime: true }): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  const date = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  if (!opts.withTime) return date;
  return `${date} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function fmtDayLabel(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  // e.g. "Thu, Apr 23 · 10:30 AM"
  const days = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];
  const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  const hh = d.getHours() % 12 || 12;
  const mm = String(d.getMinutes()).padStart(2, "0");
  const ampm = d.getHours() >= 12 ? "PM" : "AM";
  return `${days[d.getDay()]}, ${months[d.getMonth()]} ${d.getDate()} · ${hh}:${mm} ${ampm}`;
}

export function fmtRelative(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso).getTime();
  if (isNaN(d)) return "";
  const diffMs = Date.now() - d;
  const m = Math.floor(diffMs / 60000);
  if (m < 1)  return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const days = Math.floor(h / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo ago`;
  return `${Math.floor(months / 12)}y ago`;
}

export function initials(name: string | null | undefined): string {
  if (!name) return "?";
  const parts = name.replace(/\./g, " ").split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export function avatarStack(names: (string | null | undefined)[], max = 3): string {
  const items = names.filter((n): n is string => Boolean(n)).slice(0, max);
  return items.map((n) => `<span class="avatar" title="${esc(n)}">${esc(initials(n))}</span>`).join("");
}

// ── pills ────────────────────────────────────────────────────────────────
export function healthPill(status: string | null | undefined): string {
  if (!status) return "";
  const s = status.toLowerCase();
  const cls = s === "at_risk" ? "pill-risk"
           : s === "healthy"  ? "pill-healthy"
           : s === "dnc"      ? "pill-dnc"
           : s === "concerned" ? "pill-concerned"
           : "pill-unknown";
  const label = s === "at_risk" ? "Growth At risk"
              : s === "dnc"     ? "DNC"
              : status.charAt(0).toUpperCase() + status.slice(1);
  return `<span class="pill ${cls}">${esc(label)}</span>`;
}

export function priorityPill(p: string | null | undefined): string {
  if (!p) return "";
  const s = p.toLowerCase();
  const cls = s === "high" ? "pill-high" : s === "low" ? "pill-low" : "pill-med";
  return `<span class="pill ${cls}">${esc(p)}</span>`;
}

export function statusPill(s: string | null | undefined): string {
  if (!s) return "";
  const k = s.toLowerCase();
  const cls = k === "open" ? "pill-open"
           : k === "in_progress" ? "pill-progress"
           : k === "resolved" ? "pill-resolved"
           : k === "attention" ? "pill-attention"
           : "pill-info";
  const label = k === "in_progress" ? "In progress" : s.charAt(0).toUpperCase() + s.slice(1);
  return `<span class="pill ${cls}">${esc(label)}</span>`;
}

export function directionPill(d: string | null | undefined): string {
  if (!d) return "";
  const out = d.startsWith("out");
  return `<span class="pill ${out ? "pill-out" : "pill-in"}">${out ? "↗ out" : "↙ in"}</span>`;
}

export function connectedDot(c: boolean): string {
  return `<span class="inline-block w-2 h-2 rounded-full ${c ? "bg-emerald-500" : "bg-slate-300"}" title="${c ? "connected" : "no answer"}"></span>`;
}

// ── DOM helpers ──────────────────────────────────────────────────────────
export function emptyBlock(text: string): string {
  return `<div class="text-sm text-slate-500 italic py-10 text-center">${esc(text)}</div>`;
}

export function toast(msg: string): void {
  const t = $("#toast");
  if (!t) return;
  t.textContent = msg;
  t.classList.remove("hidden");
  const handle = (toast as unknown as { _t?: number })._t;
  if (handle) clearTimeout(handle);
  (toast as unknown as { _t?: number })._t = window.setTimeout(() => t.classList.add("hidden"), 2200);
}

// ── Date-range filter helpers (UI only — backend takes since/until) ─────
export type RangeKey = "today" | "yesterday" | "7d" | "30d" | "month" | "all";

export function rangeBounds(key: RangeKey): { since?: string; until?: string } {
  const now = new Date();
  const startOfDay = (d: Date) => { const x = new Date(d); x.setHours(0, 0, 0, 0); return x; };
  const endOfDay   = (d: Date) => { const x = new Date(d); x.setHours(23, 59, 59, 999); return x; };
  if (key === "today")     return { since: startOfDay(now).toISOString() };
  if (key === "yesterday") {
    const y = new Date(now); y.setDate(y.getDate() - 1);
    return { since: startOfDay(y).toISOString(), until: endOfDay(y).toISOString() };
  }
  if (key === "7d")        { const d = new Date(now); d.setDate(d.getDate() - 7);  return { since: d.toISOString() }; }
  if (key === "30d")       { const d = new Date(now); d.setDate(d.getDate() - 30); return { since: d.toISOString() }; }
  if (key === "month")     { const d = new Date(now.getFullYear(), now.getMonth(), 1); return { since: d.toISOString() }; }
  return {};
}
