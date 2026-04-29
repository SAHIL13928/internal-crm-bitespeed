// Date-range filter pills component — shared by Activity / WhatsApp / etc.
import type { RangeKey } from "../utils";
import { rangeBounds } from "../utils";

const PILLS: { key: RangeKey; label: string }[] = [
  { key: "today",     label: "Today" },
  { key: "yesterday", label: "Yesterday" },
  { key: "7d",        label: "Last 7d" },
  { key: "30d",       label: "Last 30d" },
  { key: "month",     label: "This month" },
  { key: "all",       label: "All time" },
];

export function renderRangeFilter(active: RangeKey): string {
  return `
    <div class="flex flex-wrap gap-2 items-center mb-4">
      ${PILLS.map((p) => `
        <button data-range="${p.key}"
                class="filter-pill ${active === p.key ? "filter-pill-active" : ""}">${p.label}</button>
      `).join("")}
    </div>
  `;
}

export function rangeFilterFromKey(key: RangeKey) {
  return rangeBounds(key);
}

export const DEFAULT_RANGE: RangeKey = "30d";
