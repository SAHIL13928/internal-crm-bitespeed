// Time-filter dropdown — matches the mockup's `time-filter` component:
// a button showing the current label + a popover of preset ranges.
import type { RangeKey } from "../utils";
import { RANGE_LABELS, esc } from "../utils";

const ORDER: RangeKey[] = ["today", "yesterday", "7d", "30d", "month", "quarter", "all"];

export function renderTimeFilter(id: string, active: RangeKey): string {
  return `
    <div id="${id}" class="time-filter relative" data-open="false">
      <button data-tf-toggle="${id}"
              class="inline-flex items-center gap-2 px-3 py-1.5 text-xs border border-gray-200 rounded-lg bg-white hover:border-gray-300">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>
        </svg>
        <span class="font-medium">${esc(RANGE_LABELS[active])}</span>
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 9l6 6 6-6"/></svg>
      </button>
      <div data-tf-menu="${id}"
           class="hidden absolute right-0 top-full mt-1 bg-white border border-gray-200 rounded-lg shadow-lg w-56 p-1 z-10">
        ${ORDER.map((k) => `
          <button data-tf-pick="${id}" data-range="${k}"
                  class="w-full text-left px-3 py-1.5 text-xs rounded hover:bg-gray-100 ${active === k ? "font-medium bg-gray-50" : ""}">
            ${esc(RANGE_LABELS[k])}
          </button>
        `).join("")}
      </div>
    </div>
  `;
}

export function bindTimeFilter(id: string, onPick: (k: RangeKey) => void): void {
  const root = document.getElementById(id);
  if (!root) return;
  document.querySelector<HTMLButtonElement>(`button[data-tf-toggle="${id}"]`)?.addEventListener("click", (e) => {
    e.stopPropagation();
    const menu = document.querySelector<HTMLElement>(`[data-tf-menu="${id}"]`);
    menu?.classList.toggle("hidden");
  });
  document.querySelectorAll<HTMLButtonElement>(`button[data-tf-pick="${id}"]`).forEach((b) => {
    b.addEventListener("click", () => {
      const menu = document.querySelector<HTMLElement>(`[data-tf-menu="${id}"]`);
      menu?.classList.add("hidden");
      onPick(b.dataset.range as RangeKey);
    });
  });
  // Close on outside click
  document.addEventListener("click", (e) => {
    const menu = document.querySelector<HTMLElement>(`[data-tf-menu="${id}"]`);
    if (!menu || menu.classList.contains("hidden")) return;
    if (!root.contains(e.target as Node)) menu.classList.add("hidden");
  }, { once: true });
}
