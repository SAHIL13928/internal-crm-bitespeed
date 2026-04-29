// WhatsApp tab — per-group thread view + unbound groups list.
import { Api } from "../api";
import type { ShopProfile, WhatsAppMessage } from "../types";
import { $, emptyBlock, esc, fmtAbsolute } from "../utils";

export async function renderWhatsApp(p: ShopProfile): Promise<void> {
  const body = $("#tab-body");
  if (!body) return;
  body.innerHTML = `<div class="text-xs text-gray-500">Loading…</div>`;

  const [groups, messages] = await Promise.all([
    Promise.resolve(p.whatsapp_groups || []),
    Api.waMessages(p.shop_url, 200),
  ]);
  const msgList = messages || [];

  if (groups.length === 0 && msgList.length === 0) {
    body.innerHTML = emptyBlock("No WhatsApp data linked yet.");
    return;
  }

  // Bucket messages by group
  const byGroup = new Map<string, WhatsAppMessage[]>();
  for (const m of msgList) {
    const k = m.group_name || "(direct)";
    if (!byGroup.has(k)) byGroup.set(k, []);
    byGroup.get(k)!.push(m);
  }

  const head = `<div class="mb-3 text-xs text-gray-500">
    ${groups.length} group${groups.length !== 1 ? "s" : ""} linked · ${msgList.length} message${msgList.length !== 1 ? "s" : ""} total
  </div>`;

  const seen = new Set<string>();
  const cards: string[] = [];
  for (const g of groups) {
    seen.add(g.group_name);
    cards.push(renderGroupCard(g.group_name, g.last_activity_at, byGroup.get(g.group_name) || []));
  }
  for (const [name, msgs] of byGroup.entries()) {
    if (!seen.has(name)) cards.push(renderGroupCard(name, null, msgs));
  }
  body.innerHTML = head + `<div class="space-y-3">${cards.join("")}</div>`;
}

function renderGroupCard(name: string, lastActivity: string | null, msgs: WhatsAppMessage[]): string {
  const meta = lastActivity
    ? `last activity: ${fmtAbsolute(lastActivity)}`
    : msgs.length
      ? `last activity: ${fmtAbsolute(msgs[0].timestamp)}`
      : "no messages yet";

  const body = msgs.length === 0
    ? `<div class="text-xs text-gray-400 italic mt-2">No messages received yet for this group.</div>`
    : `<div class="space-y-1.5 mt-2 max-h-72 overflow-y-auto pr-1">
         ${msgs.slice(0, 50).map(renderBubble).join("")}
       </div>`;

  return `
    <div class="bg-white border border-gray-200 rounded p-3 text-sm">
      <div class="font-medium">${esc(name)}</div>
      <div class="text-xs text-gray-500 mt-0.5">${esc(meta)} · ${msgs.length} msg${msgs.length === 1 ? "" : "s"}</div>
      ${body}
    </div>`;
}

function renderBubble(m: WhatsAppMessage): string {
  const side = m.is_from_me ? "speakerB" : "speakerA";
  const sender = m.is_from_me ? "Bitespeed" : (m.sender_name || m.sender_phone || "merchant");
  const bodyTxt = m.is_deleted
    ? `<span class="line-through text-gray-400">${esc(m.body)}</span> <span class="text-xs text-rose-500">(deleted)</span>`
    : esc(m.body || (m.message_type === "document" ? "📎 [media]" : ""));
  const editTag = m.is_edited ? ` <span class="text-xs text-gray-400">(edited)</span>` : "";
  return `
    <div class="${side} px-2 py-1.5 rounded text-sm">
      <div class="text-xs text-gray-500 mb-0.5">${esc(sender)} · ${esc(fmtAbsolute(m.timestamp))}</div>
      <div>${bodyTxt}${editTag}</div>
    </div>`;
}
