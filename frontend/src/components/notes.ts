// "Notes" tab — write-new textarea + post button + timeline.
import { Api } from "../api";
import type { NoteOut, ShopProfile } from "../types";
import { $, esc, fmtRelative, initials, toast } from "../utils";

export async function renderNotes(p: ShopProfile): Promise<void> {
  const body = $("#tab-body");
  if (!body) return;
  body.innerHTML = composer() + `<div class="text-xs text-ink-500">Loading…</div>`;
  bindComposer(p);

  const notes = (await Api.notes(p.shop_url)) || [];
  body.innerHTML = composer() + listOrEmpty(notes);
  bindComposer(p);
}

function composer(): string {
  return `
    <div class="rounded-lg border border-gray-200 bg-white p-4 mb-6">
      <textarea id="note-text" rows="2" placeholder="Add a note or follow-up..."
                class="w-full text-sm resize-none focus:outline-none placeholder:text-ink-300"></textarea>
      <div class="flex items-center justify-between mt-2">
        <label class="text-xs text-ink-500 flex items-center gap-2">
          <input id="note-followup" type="checkbox" class="rounded" /> This is a follow-up
        </label>
        <button id="note-post-btn" class="text-xs px-3 py-1.5 rounded-lg bg-ink-900 text-white hover:bg-ink-700">Post note</button>
      </div>
    </div>
  `;
}

function bindComposer(p: ShopProfile) {
  $("#note-post-btn")?.addEventListener("click", async () => {
    const ta = $<HTMLTextAreaElement>("#note-text");
    const cb = $<HTMLInputElement>("#note-followup");
    const txt = (ta?.value || "").trim();
    if (!txt) { toast("Note is empty"); return; }
    const author = window.prompt("Your name (for attribution)?") || undefined;
    const ok = await Api.createNote(p.shop_url, { body: txt, author, is_followup: cb?.checked });
    if (ok) { toast("Note posted"); void renderNotes(p); }
    else    { toast("Failed to post note"); }
  });
}

function listOrEmpty(notes: NoteOut[]): string {
  if (notes.length === 0) {
    return `<div class="rounded-lg border border-gray-200 bg-white p-6 text-sm text-ink-500 italic">No notes yet.</div>`;
  }
  return `<div class="space-y-3">${notes.map(card).join("")}</div>`;
}

function card(n: NoteOut): string {
  const followup = n.is_followup
    ? `<span class="text-[10px] px-2 py-0.5 rounded-full bg-rose-50 text-rose-700 border border-rose-100 font-medium">Follow-up${n.due_at ? ` · due ${esc(formatDue(n.due_at))}` : ""}</span>`
    : "";
  const author = n.author || "team";
  return `
    <div class="rounded-lg border border-gray-200 bg-white p-4">
      <div class="flex items-start gap-3">
        <div class="w-7 h-7 rounded-full bg-indigo-100 text-indigo-700 grid place-items-center text-xs font-semibold shrink-0">${esc(initials(author))}</div>
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2 flex-wrap">
            <div class="text-sm font-medium">${esc(author)}</div>
            <div class="text-xs text-ink-500">${esc(fmtRelative(n.created_at))}</div>
            ${followup}
          </div>
          <p class="text-sm text-ink-700 mt-1.5 whitespace-pre-wrap">${esc(n.body)}</p>
        </div>
      </div>
    </div>
  `;
}

function formatDue(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${months[d.getMonth()]} ${d.getDate()}`;
}
