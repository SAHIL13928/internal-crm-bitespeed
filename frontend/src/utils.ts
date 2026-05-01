// Pure helpers — DOM selectors, formatters, pill renderers. Match the
// mockup's bordered-pill style: `bg-X-50 text-X-700 border border-X-100`.

export const $  = <T extends HTMLElement = HTMLElement>(sel: string): T | null => document.querySelector<T>(sel);
export const $$ = <T extends HTMLElement = HTMLElement>(sel: string): T[] => Array.from(document.querySelectorAll<T>(sel));

export function esc(s: string | null | undefined): string {
  if (s === null || s === undefined) return "";
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
                  .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// Mockup format: "Thu, Apr 23 · 10:30 AM"
export function fmtDayLabel(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const hh = d.getHours() % 12 || 12;
  const mm = String(d.getMinutes()).padStart(2, "0");
  const ampm = d.getHours() >= 12 ? "PM" : "AM";
  return `${days[d.getDay()]}, ${months[d.getMonth()]} ${d.getDate()} · ${hh}:${mm} ${ampm}`;
}

// "Mon · 2:00 PM" — short variant for activity rows
export function fmtShortDay(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "—";
  const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const hh = d.getHours() % 12 || 12;
  const mm = String(d.getMinutes()).padStart(2, "0");
  const ampm = d.getHours() >= 12 ? "PM" : "AM";
  return `${days[d.getDay()]} · ${hh}:${mm} ${ampm}`;
}

// "Mon 11:04" — even shorter for the calls list
export function fmtTinyTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "—";
  const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  return `${days[d.getDay()]} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
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

export function fmtDuration(sec: number | null | undefined): string {
  if (sec == null) return "—";
  if (sec < 60) return `${Math.round(sec)}s`;
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return `${m}m ${s}s`;
}

export function initials(name: string | null | undefined): string {
  if (!name) return "?";
  const parts = name.replace(/\./g, " ").split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

// Avatar with rotating color palette so attendee stacks aren't all indigo.
const AVATAR_COLORS = [
  "bg-indigo-100 text-indigo-700",
  "bg-emerald-100 text-emerald-700",
  "bg-amber-100 text-amber-700",
  "bg-rose-100 text-rose-700",
  "bg-sky-100 text-sky-700",
  "bg-fuchsia-100 text-fuchsia-700",
];
function colorFor(seed: string): string {
  let h = 0; for (const c of seed) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return AVATAR_COLORS[h % AVATAR_COLORS.length];
}

export function avatar(name: string | null | undefined, size: "sm" | "md" = "md"): string {
  const ini = esc(initials(name));
  const cls = colorFor(name || "?");
  const dim = size === "sm" ? "w-5 h-5 text-[9px]" : "w-6 h-6 text-[10px]";
  return `<div class="${dim} rounded-full ${cls} grid place-items-center font-semibold ring-2 ring-white" title="${esc(name || "")}">${ini}</div>`;
}

export function avatarStack(names: (string | null | undefined)[], max = 3, size: "sm" | "md" = "md"): string {
  const items = names.filter((n): n is string => Boolean(n)).slice(0, max);
  if (items.length === 0) return "";
  return `<div class="flex -space-x-1 shrink-0">${items.map((n) => avatar(n, size)).join("")}</div>`;
}

// ── Pills (match mockup: filled bg + border) ────────────────────────────
export function healthPill(status: string | null | undefined): string {
  if (!status) return "";
  const s = status.toLowerCase();
  if (s === "at_risk") return `<span class="text-xs px-2 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-100 flex items-center gap-1.5"><span class="dot bg-amber-500"></span>At risk</span>`;
  if (s === "healthy") return `<span class="text-xs px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-100 flex items-center gap-1.5"><span class="dot bg-emerald-500"></span>Healthy</span>`;
  if (s === "dnc")     return `<span class="text-xs px-2 py-0.5 rounded-full bg-rose-50 text-rose-700 border border-rose-100">DNC</span>`;
  if (s === "concerned") return `<span class="text-xs px-2 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-100 flex items-center gap-1.5"><span class="dot bg-amber-500"></span>Concerned</span>`;
  return `<span class="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-ink-700 border border-gray-200">${esc(status)}</span>`;
}

export function priorityPill(p: string | null | undefined): string {
  if (!p) return "";
  const s = p.toLowerCase();
  if (s === "high") return `<span class="text-xs px-2 py-0.5 rounded-full bg-rose-50 text-rose-700 border border-rose-100 font-medium">High</span>`;
  if (s === "low")  return `<span class="text-xs px-2 py-0.5 rounded-full bg-sky-50 text-sky-700 border border-sky-100 font-medium">Low</span>`;
  return `<span class="text-xs px-2 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-100 font-medium">Med</span>`;
}

export function statusPill(s: string | null | undefined): string {
  if (!s) return "";
  const k = s.toLowerCase();
  if (k === "open")        return `<span class="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-ink-700 border border-gray-200">Open</span>`;
  if (k === "in_progress") return `<span class="text-xs px-2 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-100">In progress</span>`;
  if (k === "resolved")    return `<span class="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-100">Resolved</span>`;
  if (k === "unresolved")  return `<span class="text-[10px] px-1.5 py-0.5 rounded-full bg-rose-50 text-rose-700 border border-rose-100">Unresolved</span>`;
  if (k === "attention")   return `<span class="text-[10px] px-1.5 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-100">Attention</span>`;
  if (k === "informational") return `<span class="text-[10px] px-1.5 py-0.5 rounded-full bg-gray-100 text-ink-700 border border-gray-200">Informational</span>`;
  return `<span class="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-ink-700 border border-gray-200">${esc(s)}</span>`;
}

export function topicPill(label: string): string {
  return `<span class="text-[10px] px-1.5 py-0.5 rounded-full bg-gray-100 text-ink-700 border border-gray-200">${esc(label)}</span>`;
}

// ── Toast ────────────────────────────────────────────────────────────────
export function toast(msg: string): void {
  const t = $("#toast");
  if (!t) return;
  t.textContent = msg;
  t.classList.remove("hidden");
  const handle = (toast as unknown as { _t?: number })._t;
  if (handle) clearTimeout(handle);
  (toast as unknown as { _t?: number })._t = window.setTimeout(() => t.classList.add("hidden"), 2200);
}

// ── Date-range helpers ──────────────────────────────────────────────────
export type RangeKey = "today" | "yesterday" | "7d" | "30d" | "month" | "quarter" | "all";

export const RANGE_LABELS: Record<RangeKey, string> = {
  today: "Today",
  yesterday: "Yesterday",
  "7d": "Last 7 days",
  "30d": "Last 30 days",
  month: "This month",
  quarter: "This quarter",
  all: "All time",
};

export function rangeBounds(key: RangeKey): { since?: string; until?: string } {
  const now = new Date();
  const startOfDay = (d: Date) => { const x = new Date(d); x.setHours(0, 0, 0, 0); return x; };
  const endOfDay   = (d: Date) => { const x = new Date(d); x.setHours(23, 59, 59, 999); return x; };
  if (key === "today")     return { since: startOfDay(now).toISOString() };
  if (key === "yesterday") {
    const y = new Date(now); y.setDate(y.getDate() - 1);
    return { since: startOfDay(y).toISOString(), until: endOfDay(y).toISOString() };
  }
  if (key === "7d")      { const d = new Date(now); d.setDate(d.getDate() - 7);  return { since: d.toISOString() }; }
  if (key === "30d")     { const d = new Date(now); d.setDate(d.getDate() - 30); return { since: d.toISOString() }; }
  if (key === "month")   { const d = new Date(now.getFullYear(), now.getMonth(), 1); return { since: d.toISOString() }; }
  if (key === "quarter") {
    const q = Math.floor(now.getMonth() / 3);
    const d = new Date(now.getFullYear(), q * 3, 1);
    return { since: d.toISOString() };
  }
  return {};
}
