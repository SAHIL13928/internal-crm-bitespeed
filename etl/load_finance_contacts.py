"""Load `data/inputs/Finance Contacts - Sheet1.csv` into the contacts table.

The sheet is free-text inside each `POC Details` cell — multi-line, mixed
phones / names / emails / annotations like "do not call" or "(founder)".
We parse defensively: extract every email and every plausible phone, then
attach a best-effort `name` taken from the surrounding text on the same
line.

Idempotent: a contact row with the same (shop_url, phone) or
(shop_url, email) is only added if it doesn't already exist. Re-running
this loader after the master CSV has been (re)loaded is safe — it tops
up rather than replacing.

Why a separate loader and not part of `load_shops.py`?
  - `load_shops.py` wipes all of a shop's children on reload. We don't
    want a master-CSV reload to nuke finance contacts that came from a
    different sheet entirely.
  - The schema differs (per-cell free-text vs. semicolon-delimited
    fields), so the parsing logic is materially different.
"""
from __future__ import annotations

import csv
import logging
import os
import re
import sys
from typing import Iterable, Optional

# Make repo root importable when run as `python etl/...`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session  # noqa: E402

from crm_app.db import Base, SessionLocal, engine  # noqa: E402
from crm_app.models import Contact, Shop  # noqa: E402
from crm_app.utils import norm_phone  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("load_finance_contacts")

CSV_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "inputs", "Finance Contacts - Sheet1.csv",
)

# Regex pulls out one *plausible* phone — runs of digits + spaces/hyphens
# starting with optional `+` and reaching ≥10 digits after stripping
# non-digits. We re-validate the digit count after cleanup.
_PHONE_RE = re.compile(r"\+?[\d][\d\s().\- ‪‬]{8,}\d")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Bidi formatting chars + NBSP that LLMs / spreadsheets sometimes inject.
_FMT_CHARS = re.compile(r"[‪-‮ ]")
# A "name" candidate is anything alphabetic + spaces — accept dots/apostrophes.
_NAME_CHARS = re.compile(r"[A-Za-z][A-Za-z .'\-]{1,}")
# Common annotations that should NOT become names.
_NAME_STOPWORDS = {
    "poc", "founder", "founders", "manager", "accounts", "accountant",
    "do not call", "donotcall", "dnd", "new poc", "and", "the",
    # Position words occasionally appearing solo
    "owner", "ceo", "co-founder", "cofounder", "ji", "sir", "madam",
}


def _clean_text(s: str) -> str:
    if not s:
        return ""
    return _FMT_CHARS.sub(" ", s).strip()


def _looks_like_phone(raw_match: str) -> Optional[str]:
    """Return digits-only form (≥10 digits) or None."""
    digits = re.sub(r"\D+", "", raw_match)
    if len(digits) < 10:
        return None
    # Cap at 15 digits (E.164 max) — anything longer is likely two phones
    # mashed together by the regex; take the trailing 10–13 as a guess.
    if len(digits) > 15:
        digits = digits[-13:]
    return digits


def _phones_in(s: str) -> list[str]:
    out = []
    for m in _PHONE_RE.finditer(s):
        ph = _looks_like_phone(m.group(0))
        if ph and ph not in out:
            out.append(ph)
    return out


def _emails_in(s: str) -> list[str]:
    return list({m.group(0).strip().lower() for m in _EMAIL_RE.finditer(s)})


def _name_near(snippet: str) -> Optional[str]:
    """Best-effort name pulled from a fragment that already had its phones
    and emails removed. Returns None if nothing plausible remains."""
    s = snippet.strip(" -–—:;,.\t\n")
    if not s:
        return None
    # Strip leading/trailing punctuation chunks like "(founder)".
    s = re.sub(r"\([^)]+\)", " ", s).strip()
    # Split on dashes / colons first so "POC - abitha" → ["POC", "abitha"].
    # We deliberately don't split on spaces because real names have spaces.
    chunks = re.split(r"\s*[-–—:]+\s*", s)
    candidates: list[str] = []
    for chunk in chunks:
        chunk = chunk.strip(" .'-")
        if not chunk:
            continue
        # Drop chunks that are entirely stopwords (case-insensitive).
        normalized = chunk.lower().strip()
        if normalized in _NAME_STOPWORDS:
            continue
        # Must contain at least one alpha char.
        if not re.search(r"[A-Za-z]", chunk):
            continue
        candidates.append(chunk)

    if not candidates:
        return None
    # Prefer the SHORTEST candidate that's at least 2 chars — names are
    # usually one or two words; the longest chunk is often a multi-token
    # blob like "New POC - Alay shah" that survived a missed delimiter.
    candidates.sort(key=len)
    name = next((c for c in candidates if len(c) >= 2), None)
    if name is None:
        return None
    return name.title() if name.isupper() else name


def parse_cell(cell: str) -> list[dict]:
    """Returns a list of {phone, email, name} dicts (any of the three may
    be None). One dict per logical contact found in the cell."""
    cell = _clean_text(cell)
    if not cell:
        return []

    # Split into rough "entries" by newlines + semicolons. Commas are a
    # poor delimiter (used inside emails and lists) so we don't split on
    # them at the top level.
    pieces = re.split(r"[\n;]+", cell)
    contacts: list[dict] = []

    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue

        emails = _emails_in(piece)
        phones = _phones_in(piece)

        # Carve emails + phones out of the piece so what's left is a
        # candidate name fragment.
        residue = _PHONE_RE.sub(" ", _EMAIL_RE.sub(" ", piece))
        name = _name_near(residue)

        # If we have multiple phones in one piece (e.g. "9810416655 Divyam,
        # 9810416655 Ajay") it's likely the parser missed a delimiter —
        # fall back to splitting on commas.
        if len(phones) > 1:
            sub_pieces = re.split(r",", piece)
            if len(sub_pieces) > 1:
                for sp in sub_pieces:
                    contacts.extend(parse_cell(sp))
                continue

        if not (emails or phones or name):
            continue

        # Emit one contact per email and per phone (or one bare-name row
        # if nothing else). We deliberately duplicate the name across
        # them so downstream display is consistent.
        emitted = False
        for em in emails:
            contacts.append({"phone": None, "email": em, "name": name})
            emitted = True
        for ph in phones:
            contacts.append({"phone": ph, "email": None, "name": name})
            emitted = True
        if not emitted and name:
            contacts.append({"phone": None, "email": None, "name": name})

    return contacts


def load_finance_contacts(csv_path: str = CSV_PATH) -> dict:
    Base.metadata.create_all(bind=engine)
    db: Session = SessionLocal()
    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        rows_seen = 0
        unknown_shops: list[str] = []
        new_phone = new_email = new_name_only = 0
        skipped_dupe = 0
        empty_cells = 0
        unparseable = 0

        # Pre-cache phone/email sets per shop so dedupe is O(1).
        # Key: (shop_url, normalized_phone) -> True; (shop_url, lower_email) -> True
        phone_keys: set[tuple[str, str]] = set()
        email_keys: set[tuple[str, str]] = set()
        name_only_keys: set[tuple[str, str]] = set()
        for c in db.query(Contact.shop_url, Contact.phone, Contact.email, Contact.name).all():
            shop_url, phone, email, name = c
            if not shop_url:
                continue
            if phone:
                ph = norm_phone(phone)
                if ph:
                    phone_keys.add((shop_url, ph))
            if email:
                email_keys.add((shop_url, email.lower()))
            if name and not phone and not email:
                name_only_keys.add((shop_url, name.strip().lower()))

        for row in rows:
            rows_seen += 1
            shop_url = (row.get("Store URL") or "").strip().lower()
            details = row.get("POC Details") or ""
            if not shop_url:
                continue

            # Confirm shop exists; skip orphan rows but track for the report.
            if db.get(Shop, shop_url) is None:
                unknown_shops.append(shop_url)
                continue

            if not details.strip():
                empty_cells += 1
                continue

            parsed = parse_cell(details)
            if not parsed:
                unparseable += 1
                continue

            for item in parsed:
                ph = item.get("phone")
                em = item.get("email")
                name = item.get("name")

                if ph:
                    # Store with the leading "+" stripped — keep raw digits;
                    # phone_to_shop builder normalizes anyway.
                    if (shop_url, ph) in phone_keys:
                        skipped_dupe += 1
                        continue
                    db.add(Contact(
                        shop_url=shop_url, phone=ph, name=name,
                        is_internal=False, role="finance",
                    ))
                    phone_keys.add((shop_url, ph))
                    new_phone += 1
                elif em:
                    if (shop_url, em) in email_keys:
                        skipped_dupe += 1
                        continue
                    db.add(Contact(
                        shop_url=shop_url, email=em, name=name,
                        is_internal=False, role="finance",
                    ))
                    email_keys.add((shop_url, em))
                    new_email += 1
                elif name:
                    key = (shop_url, name.strip().lower())
                    if key in name_only_keys:
                        skipped_dupe += 1
                        continue
                    db.add(Contact(
                        shop_url=shop_url, name=name,
                        is_internal=False, role="finance",
                    ))
                    name_only_keys.add(key)
                    new_name_only += 1

        db.commit()

        log.info("rows seen:          %d", rows_seen)
        log.info("empty cells:        %d", empty_cells)
        log.info("unparseable cells:  %d", unparseable)
        log.info("unknown shop URLs:  %d (skipped)", len(unknown_shops))
        log.info("new phone contacts: %d", new_phone)
        log.info("new email contacts: %d", new_email)
        log.info("new name-only:      %d", new_name_only)
        log.info("skipped dupes:      %d", skipped_dupe)

        return {
            "rows_seen": rows_seen,
            "empty_cells": empty_cells,
            "unparseable": unparseable,
            "unknown_shops": unknown_shops,
            "new_phone": new_phone,
            "new_email": new_email,
            "new_name_only": new_name_only,
            "skipped_dupes": skipped_dupe,
        }
    finally:
        db.close()


if __name__ == "__main__":
    load_finance_contacts()
