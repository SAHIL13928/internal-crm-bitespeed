// Pure helpers — no DOM, no fetch.

export const $ = <T extends HTMLElement = HTMLElement>(sel: string): T | null =>
  document.querySelector<T>(sel);

export const $$ = <T extends HTMLElement = HTMLElement>(sel: string): T[] =>
  Array.from(document.querySelectorAll<T>(sel));

export function esc(s: string | null | undefined): string {
  if (s === null || s === undefined) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function fmtAbsolute(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  // YYYY-MM-DD HH:MM in local time
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function fmtRelative(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso).getTime();
  if (isNaN(d)) return "";
  const diffMs = Date.now() - d;
  const m = Math.floor(diffMs / 60000);
  if (m < 1) return "just now";
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
  if (!name) return "";
  const parts = name.replace(/\./g, " ").split(/\s+/).filter(Boolean);
  if (!parts.length) return "";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export function emptyBlock(text: string): string {
  return `<div class="text-sm text-gray-500 italic py-6 text-center">${esc(text)}</div>`;
}

export function toast(msg: string): void {
  const t = $("#toast");
  if (!t) return;
  t.textContent = msg;
  t.classList.remove("hidden");
  // single-flight: cancel prior timeout
  const handle = (toast as unknown as { _t?: number })._t;
  if (handle) clearTimeout(handle);
  (toast as unknown as { _t?: number })._t = window.setTimeout(() => {
    t.classList.add("hidden");
  }, 2200);
}

// Direction / connected pills used across calls + timeline.
export function directionPill(direction: string | null | undefined): string {
  if (!direction) return "";
  const out = direction.startsWith("out");
  return `<span class="pill ${out ? "bg-blue-50 text-blue-700" : "bg-purple-50 text-purple-700"}">${out ? "↗ out" : "↙ in"}</span>`;
}

export function connectedDot(c: boolean): string {
  return `<span class="inline-block w-2 h-2 rounded-full ${c ? "bg-emerald-500" : "bg-gray-300"}" title="${c ? "connected" : "no answer"}"></span>`;
}

export function healthPill(s: string | null | undefined): string {
  if (!s) return "";
  const map: Record<string, string> = {
    healthy: "bg-emerald-50 text-emerald-700",
    at_risk: "bg-amber-50 text-amber-700",
    dnc: "bg-rose-50 text-rose-700",
    unknown: "bg-gray-100 text-gray-600",
  };
  const cls = map[s.toLowerCase()] || "bg-gray-100 text-gray-700";
  return `<span class="pill ${cls}">${esc(s)}</span>`;
}
