// "WhatsApp" tab — group cards with AI summary placeholder + topic pills + thread view.
import { Api } from "../api";
import type { ShopProfile, WhatsAppMessage } from "../types";
import { $, emptyBlock, esc, fmtAbsolute, fmtRelative, statusPill } from "../utils";

// Topic extraction is a TODO (no NLP yet) — for now we surface frequently-mentioned
// keywords as a static list when the messages don't yield useful tokens. The
// reference design shows pills like "pricing", "billing"; here we approximate.
const PLACEHOLDER_TOPICS = ["activity", "support"];

export async function renderWhatsApp(p: ShopProfile): Promise<void> {
  const body = $("#tab-body");
  if (!body) return;
  body.innerHTML = `<div class="text-xs text-slate-500">Loading…</div>`;

  const groups = p.whatsapp_groups || [];
  const messages = (await Api.waMessages(p.shop_url, 200)) || [];

  if (groups.length === 0 && messages.length === 0) {
    body.innerHTML = emptyBlock("No WhatsApp data linked yet for this merchant.");
    return;
  }

  const totalMsgs = messages.length;
  const lastMsg = messages[0]?.timestamp;

  // Bucket messages by group_name
  const byGroup = new Map<string, WhatsAppMessage[]>();
  for (const m of messages) {
    const k = m.group_name || "(direct)";
    if (!byGroup.has(k)) byGroup.set(k, []);
    byGroup.get(k)!.push(m);
  }

  // AI summary box — placeholder until we wire an LLM pass over recent messages.
  const aiBox = `
    <div class="ai-box mb-5">
      <div class="text-[11px] uppercase tracking-wide text-indigo-600 mb-2">AI state summary <span class="text-slate-400 normal-case">(WIP)</span></div>
      <div class="text-sm text-slate-700 leading-relaxed">
        ${totalMsgs} message${totalMsgs === 1 ? "" : "s"} across ${byGroup.size} group${byGroup.size === 1 ? "" : "s"}.
        ${lastMsg ? `Last activity ${esc(fmtRelative(lastMsg))}.` : ""}
        Auto-generated topic and sentiment summaries land here once the LLM pass is wired.
      </div>
      <div class="flex flex-wrap gap-1.5 mt-3">
        ${PLACEHOLDER_TOPICS.map((t) => `<span class="topic">${esc(t)}</span>`).join("")}
      </div>
    </div>
  `;

  // Render: known groups first (whether or not they have messages),
  // then any extra groups we only learned about via messages.
  const seen = new Set<string>();
  const groupCards: string[] = [];
  for (const g of groups) {
    seen.add(g.group_name);
    groupCards.push(renderGroupCard(g.group_name, g.last_activity_at, byGroup.get(g.group_name) || []));
  }
  for (const [name, msgs] of byGroup.entries()) {
    if (!seen.has(name)) groupCards.push(renderGroupCard(name, null, msgs));
  }

  body.innerHTML = aiBox + `<div class="space-y-3">${groupCards.join("")}</div>`;
}

function renderGroupCard(name: string, lastActivity: string | null, msgs: WhatsAppMessage[]): string {
  const meta = lastActivity
    ? `last activity ${fmtRelative(lastActivity)}`
    : msgs.length > 0
      ? `last activity ${fmtRelative(msgs[0].timestamp)}`
      : "no messages yet";

  // Status badge: "Resolved" if we have messages, "Attention" if we don't.
  // (Real conversation classification is a TODO.)
  const status = msgs.length > 0 ? statusPill("resolved") : statusPill("attention");

  const body = msgs.length === 0
    ? `<div class="text-xs text-slate-400 italic mt-2">No messages received yet.</div>`
    : `<div class="space-y-1.5 mt-3 max-h-72 overflow-y-auto pr-1">
         ${msgs.slice(0, 50).map(renderBubble).join("")}
       </div>`;

  return `
    <div class="card p-4">
      <div class="flex items-start justify-between gap-3">
        <div class="min-w-0 flex-1">
          <div class="flex items-center gap-2">
            <h4 class="text-sm font-medium truncate">${esc(name)}</h4>
            ${status}
          </div>
          <div class="text-xs text-slate-500 mt-0.5">${esc(meta)} · ${msgs.length} msg${msgs.length === 1 ? "" : "s"}</div>
        </div>
      </div>
      ${body}
    </div>
  `;
}

function renderBubble(m: WhatsAppMessage): string {
  const side = m.is_from_me ? "speakerB" : "speakerA";
  const sender = m.is_from_me ? "Bitespeed" : (m.sender_name || m.sender_phone || "merchant");
  const bodyTxt = m.is_deleted
    ? `<span class="line-through text-slate-400">${esc(m.body)}</span> <span class="text-xs text-rose-500">(deleted)</span>`
    : esc(m.body || (m.message_type === "document" ? "📎 [media]" : ""));
  const editTag = m.is_edited ? ` <span class="text-xs text-slate-400">(edited)</span>` : "";
  return `
    <div class="${side} px-2 py-1.5 rounded text-sm">
      <div class="text-xs text-slate-500 mb-0.5">${esc(sender)} · ${esc(fmtAbsolute(m.timestamp))}</div>
      <div>${bodyTxt}${editTag}</div>
    </div>`;
}
