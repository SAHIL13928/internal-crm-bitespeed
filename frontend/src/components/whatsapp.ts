// "WhatsApp" tab — Core group summary card (AI summary + open issue + topics)
// followed by a Conversations list. Layout matches the mockup exactly.
import { Api } from "../api";
import type { ShopProfile, WhatsAppMessage } from "../types";
import { $, avatarStack, esc, fmtRelative, healthPill, statusPill, topicPill } from "../utils";

interface ConversationGroup {
  name: string;
  status: "unresolved" | "resolved" | "attention" | "informational";
  msgs: WhatsAppMessage[];
  topics: string[];
  participants: string[];
  preview: string;
}

export async function renderWhatsApp(p: ShopProfile): Promise<void> {
  const body = $("#tab-body");
  if (!body) return;
  body.innerHTML = `<div class="text-xs text-ink-500">Loading…</div>`;

  const messages = (await Api.waMessages(p.shop_url, 300)) || [];
  const groups = p.whatsapp_groups || [];

  if (groups.length === 0 && messages.length === 0) {
    body.innerHTML = `<div class="rounded-lg border border-gray-200 bg-white p-6 text-sm text-ink-500 italic">No WhatsApp data linked yet for this merchant.</div>`;
    return;
  }

  // Bucket messages by group
  const byGroup = new Map<string, WhatsAppMessage[]>();
  for (const m of messages) {
    const k = m.group_name || "(direct)";
    if (!byGroup.has(k)) byGroup.set(k, []);
    byGroup.get(k)!.push(m);
  }

  // Pick a "core" group: the one with the most messages.
  const core = pickCoreGroup(byGroup, groups);

  // Build conversation list
  const convos = buildConversations(byGroup);

  body.innerHTML = `
    ${coreCard(p, core, messages.length, byGroup.size)}

    <div class="flex items-center justify-between mb-4 mt-6">
      <h2 class="text-base font-semibold">Conversations</h2>
      <div class="text-xs text-ink-500">${convos.length} group${convos.length === 1 ? "" : "s"}</div>
    </div>
    <div class="text-xs text-ink-500 mb-3">${messages.length} message${messages.length === 1 ? "" : "s"} across ${byGroup.size} group${byGroup.size === 1 ? "" : "s"}</div>

    ${convos.length === 0
      ? `<div class="rounded-lg border border-gray-200 bg-white p-6 text-sm text-ink-500 italic">No conversations to show.</div>`
      : `<div class="rounded-lg border border-gray-200 bg-white divide-y divide-gray-100">${convos.map(convoRow).join("")}</div>`}
  `;
}

// ── Core group card ─────────────────────────────────────────────────────
function coreCard(p: ShopProfile, core: ConversationGroup | null, totalMsgs: number, groupCount: number): string {
  const headerName = core ? esc(core.name) : `${esc(p.brand_name || p.shop_url)} — Core group`;
  const memberCount = core ? `${core.participants.length} member${core.participants.length === 1 ? "" : "s"}` : "—";
  const lastActivity = core?.msgs[0]?.timestamp ? fmtRelative(core.msgs[0].timestamp) : "no messages yet";

  const aiSummary = core
    ? `${totalMsgs} message${totalMsgs === 1 ? "" : "s"} across ${groupCount} group${groupCount === 1 ? "" : "s"} for this merchant. Recent activity ${lastActivity}. Topic and sentiment classification pending — wire LLM pass over recent messages.`
    : "No messages received yet for this merchant.";

  const openIssueBlock = `
    <div class="rounded-md border border-gray-100 bg-gray-50 px-3 py-2 text-xs text-ink-500">
      No open WhatsApp-sourced issues right now.
    </div>
  `;

  const topicsHtml = core && core.topics.length > 0
    ? core.topics.slice(0, 6).map(topicPill).join("")
    : ["activity", "support"].map(topicPill).join("");

  return `
    <section class="rounded-lg border border-gray-200 bg-white p-5 mb-6">
      <div class="flex items-start justify-between gap-6">
        <div class="min-w-0">
          <div class="flex items-center gap-2 flex-wrap">
            <h2 class="text-base font-semibold truncate">${headerName}</h2>
            <span class="text-xs text-ink-500">· ${memberCount}</span>
          </div>
          <div class="text-xs text-ink-500 mt-0.5">Last activity ${esc(lastActivity)}</div>
        </div>
        ${healthPill(p.health_status)}
      </div>

      <div class="grid grid-cols-3 gap-4 mt-5">
        <div class="col-span-2">
          <div class="text-[11px] uppercase tracking-wide text-ink-500 font-medium mb-2">AI state summary</div>
          <p class="text-sm text-ink-700 leading-relaxed">${esc(aiSummary)}</p>
        </div>
        <div>
          <div class="text-[11px] uppercase tracking-wide text-ink-500 font-medium mb-2">Open issue</div>
          ${openIssueBlock}
          <div class="text-[11px] uppercase tracking-wide text-ink-500 font-medium mt-4 mb-2">Topics this week</div>
          <div class="flex flex-wrap gap-1.5">${topicsHtml}</div>
        </div>
      </div>
    </section>
  `;
}

// ── Conversation list rows ──────────────────────────────────────────────
function convoRow(c: ConversationGroup): string {
  const stack = avatarStack(c.participants, 3);
  const dotColor = c.status === "unresolved" || c.status === "attention" ? "bg-amber-500"
                : c.status === "resolved" ? "bg-emerald-500" : "bg-gray-300";
  const dateRange = formatDateRange(c.msgs);
  return `
    <div class="p-4 hover:bg-gray-50 cursor-pointer">
      <div class="flex items-start justify-between gap-4">
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2 flex-wrap">
            <div class="text-sm font-medium truncate">${esc(c.name)}</div>
            ${statusPill(c.status)}
            <span class="dot ${dotColor}"></span>
          </div>
          <div class="text-xs text-ink-500 mt-1">${esc(dateRange)} · ${c.msgs.length} message${c.msgs.length === 1 ? "" : "s"}</div>
          ${c.preview ? `<div class="text-sm text-ink-700 mt-2 line-clamp-2">${esc(c.preview)}</div>` : ""}
          ${c.topics.length > 0 ? `<div class="flex flex-wrap gap-1.5 mt-2">${c.topics.slice(0, 4).map(topicPill).join("")}</div>` : ""}
        </div>
        ${stack}
      </div>
    </div>
  `;
}

// ── Helpers ─────────────────────────────────────────────────────────────
function pickCoreGroup(byGroup: Map<string, WhatsAppMessage[]>, groups: { group_name: string }[]): ConversationGroup | null {
  if (byGroup.size === 0) return null;
  let best: { name: string; msgs: WhatsAppMessage[] } | null = null;
  for (const [name, msgs] of byGroup) {
    if (!best || msgs.length > best.msgs.length) best = { name, msgs };
  }
  if (!best) return null;
  return summarizeGroup(best.name, best.msgs);
}

function buildConversations(byGroup: Map<string, WhatsAppMessage[]>): ConversationGroup[] {
  const out: ConversationGroup[] = [];
  for (const [name, msgs] of byGroup) {
    out.push(summarizeGroup(name, msgs));
  }
  // Sort by most recent message first
  out.sort((a, b) => {
    const ta = a.msgs[0]?.timestamp ? new Date(a.msgs[0].timestamp).getTime() : 0;
    const tb = b.msgs[0]?.timestamp ? new Date(b.msgs[0].timestamp).getTime() : 0;
    return tb - ta;
  });
  return out;
}

function summarizeGroup(name: string, msgs: WhatsAppMessage[]): ConversationGroup {
  const sorted = [...msgs].sort((a, b) => {
    const ta = a.timestamp ? new Date(a.timestamp).getTime() : 0;
    const tb = b.timestamp ? new Date(b.timestamp).getTime() : 0;
    return tb - ta;
  });
  const lastMerchantMsg = sorted.find((m) => !m.is_from_me);
  const preview = lastMerchantMsg?.body || sorted[0]?.body || "";
  const participants = Array.from(new Set(sorted.map((m) => m.sender_name || m.sender_phone || "").filter(Boolean))).slice(0, 5);

  // Topic guess — naive keyword pick from the message bodies
  const topics = guessTopics(sorted);

  // Status heuristic until classifier is wired:
  // - Recent merchant message + we haven't responded → "unresolved"
  // - Otherwise → "resolved"
  let status: ConversationGroup["status"] = "resolved";
  if (lastMerchantMsg && sorted[0] && !sorted[0].is_from_me) {
    const ageMs = Date.now() - new Date(lastMerchantMsg.timestamp || 0).getTime();
    if (ageMs < 24 * 3600 * 1000) status = "unresolved";
    else if (ageMs < 7 * 86400 * 1000) status = "attention";
    else status = "informational";
  }

  return { name, msgs: sorted, status, topics, participants, preview };
}

const TOPIC_KEYWORDS = [
  "billing", "invoice", "payment", "credit", "refund",
  "pricing", "price", "discount",
  "shipping", "delivery", "logistics",
  "integration", "api", "webhook",
  "bulk", "import", "export", "csv",
  "feature", "roadmap", "request",
  "competitor", "alternative",
  "renewal", "contract", "subscription",
  "support", "issue", "bug", "error",
  "onboarding", "training",
];

function guessTopics(msgs: WhatsAppMessage[]): string[] {
  const text = msgs.slice(0, 50).map((m) => (m.body || "").toLowerCase()).join(" ");
  const seen = new Set<string>();
  for (const k of TOPIC_KEYWORDS) {
    if (text.includes(k)) seen.add(k);
    if (seen.size >= 6) break;
  }
  return Array.from(seen);
}

function formatDateRange(msgs: WhatsAppMessage[]): string {
  if (msgs.length === 0) return "—";
  const newest = msgs[0]?.timestamp;
  const oldest = msgs[msgs.length - 1]?.timestamp;
  if (!newest) return "—";
  if (!oldest || newest === oldest) return fmtRelative(newest);
  return `${fmtRelative(oldest)} → ${fmtRelative(newest)}`;
}
