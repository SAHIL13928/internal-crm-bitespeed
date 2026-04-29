// "Notes" tab — timeline of post-meeting notes / follow-ups.
import { Api } from "../api";
import type { ShopProfile, NoteOut } from "../types";
import { $, emptyBlock, esc, fmtAbsolute, fmtRelative, initials, toast } from "../utils";

export async function renderNotes(p: ShopProfile): Promise<void> {
  const body = $("#tab-body");
  if (!body) return;
  body.innerHTML = topBar() + `<div class="text-xs text-slate-500 mt-3">Loading…</div>`;
  bind(p);

  const notes = (await Api.notes(p.shop_url)) || [];
  body.innerHTML = topBar() + listOrEmpty(notes);
  bind(p);
}

function topBar(): string {
  return `
    <div class="flex items-center justify-between mb-4">
      <div class="text-xs text-slate-500">Internal notes &amp; follow-ups</div>
      <button id="new-note-btn" class="text-sm px-3 py-1.5 bg-slate-900 text-white rounded hover:bg-slate-800">+ Add note</button>
    </div>
  `;
}

function bind(p: ShopProfile): void {
  $("#new-note-btn")?.addEventListener("click", () => promptNewNote(p));
}

function listOrEmpty(notes: NoteOut[]): string {
  if (notes.length === 0) return emptyBlock("No notes yet.");
  return `<div class="space-y-2">${notes.map(card).join("")}</div>`;
}

function card(n: NoteOut): string {
  const initial = `<span class="avatar">${esc(initials(n.author))}</span>`;
  const dueLine = n.is_followup
    ? `<div class="text-xs mt-1 ${n.due_at ? "text-amber-700" : "text-slate-500"}">Follow-up${n.due_at ? ` due ${esc(fmtAbsolute(n.due_at, { withTime: false }))}` : ""}</div>`
    : "";
  return `
    <div class="card p-3 flex gap-3">
      <div class="pt-0.5">${initial}</div>
      <div class="flex-1 min-w-0">
        <div class="flex items-baseline justify-between">
          <div class="text-sm font-medium">${esc(n.author || "team")}</div>
          <div class="text-xs text-slate-500">${esc(fmtRelative(n.created_at))}</div>
        </div>
        <div class="text-sm text-slate-700 mt-1 whitespace-pre-wrap">${esc(n.body)}</div>
        ${dueLine}
      </div>
    </div>
  `;
}

async function promptNewNote(p: ShopProfile): Promise<void> {
  const note = window.prompt("Note?");
  if (!note) return;
  const author = window.prompt("Author (your name)?") || undefined;
  const ok = await Api.createNote(p.shop_url, { body: note, author });
  if (ok) { toast("Note saved"); void renderNotes(p); }
  else    { toast("Failed to save note"); }
}
