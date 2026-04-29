import os
import csv
import time
import json
import re
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("FIREFLIES_API_KEY")
URL = "https://api.fireflies.ai/graphql"
HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}

SRC_CSV = "all_meet_links_organized.csv"
OUT_CSV = "all_meet_links_organized_v2.csv"
FAIL_LOG = "failed_meetings.log"

BATCH_SIZE = 10
BATCH_SLEEP = 2
BACKOFFS = [30, 60, 120]

api_calls = 0


def build_batched_query(ids):
    fragments = []
    for i, mid in enumerate(ids):
        safe = mid.replace('"', '\\"')
        fragments.append(
            f'm{i}: transcript(id: "{safe}") {{ '
            f'id title date meeting_link '
            f'meeting_attendees {{ displayName email name }} '
            f'}}'
        )
    return "query {\n  " + "\n  ".join(fragments) + "\n}"


def fetch_batch(ids):
    global api_calls
    query = {"query": build_batched_query(ids)}
    for attempt in range(len(BACKOFFS) + 1):
        api_calls += 1
        try:
            resp = requests.post(URL, json=query, headers=HEADERS, timeout=60)
        except requests.RequestException as e:
            if attempt < len(BACKOFFS):
                sleep_for = BACKOFFS[attempt]
                print(f"  request error ({e}); sleeping {sleep_for}s ...")
                time.sleep(sleep_for)
                continue
            return None, f"request error: {e}"

        if resp.status_code == 429:
            if attempt < len(BACKOFFS):
                sleep_for = BACKOFFS[attempt]
                print(f"  429 rate-limited; sleeping {sleep_for}s ...")
                time.sleep(sleep_for)
                continue
            return None, "429 exhausted"

        if resp.status_code != 200:
            if attempt < len(BACKOFFS):
                sleep_for = BACKOFFS[attempt]
                print(f"  http {resp.status_code}; sleeping {sleep_for}s ...")
                time.sleep(sleep_for)
                continue
            return None, f"http {resp.status_code}: {resp.text[:200]}"

        try:
            data = resp.json()
        except ValueError:
            return None, "invalid json"

        return data, None

    return None, "retries exhausted"


def parse_transcript(t):
    if not t:
        return None
    meeting_link = t.get("meeting_link") or ""
    title = t.get("title", "") or ""
    date_ms = t.get("date")
    date_str = ""
    if date_ms:
        dt = datetime.fromtimestamp(date_ms / 1000, tz=timezone.utc)
        date_str = dt.strftime("%Y-%m-%d %H:%M")

    company = ""
    m = re.match(r"^(.+?)\s*<>\s*[Bb]ite[Ss]peed", title)
    if not m:
        m = re.match(r"^[Bb]ite[Ss]peed\s*<>\s*(.+?)(\s*\[|\s*\||$)", title)
    if not m:
        m = re.match(r"^(.+?)\s*<>\s*", title)
    if m:
        company = m.group(1).strip()

    atts = t.get("meeting_attendees") or []
    external, internal = [], []
    for a in atts:
        email = a.get("email") or ""
        name = a.get("name") or a.get("displayName") or ""
        if email.lower().endswith("@bitespeed.co"):
            internal.append(email)
        elif email:
            external.append(f"{name} <{email}>" if name else email)

    return {
        "company": company,
        "date": date_str,
        "meeting_title": title,
        "meeting_link": meeting_link,
        "external_attendees": "; ".join(external),
        "internal_attendees": "; ".join(internal),
        "meeting_id": t.get("id", ""),
    }


def main():
    t0 = time.time()

    with open(SRC_CSV, "r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    fieldnames = [
        "company", "date", "meeting_title", "meeting_link",
        "external_attendees", "internal_attendees", "meeting_id",
    ]

    missing_rows = []
    for r in rows:
        if not (r.get("meeting_link") or "").strip() or not (r.get("external_attendees") or "").strip():
            if r.get("meeting_id"):
                missing_rows.append(r)

    missing_ids = [r["meeting_id"] for r in missing_rows]
    starting_missing = len(missing_ids)
    print(f"Starting missing count: {starting_missing}")
    print(f"Total rows in CSV: {len(rows)}")

    if starting_missing == 0:
        print("Nothing to refetch.")
        return

    refetched = {}
    failed = []

    batches = [missing_ids[i:i + BATCH_SIZE] for i in range(0, len(missing_ids), BATCH_SIZE)]
    print(f"Will issue {len(batches)} batched GraphQL calls (batch size {BATCH_SIZE}).")

    for bi, batch in enumerate(batches, 1):
        print(f"Batch {bi}/{len(batches)} ({len(batch)} ids)")
        data, err = fetch_batch(batch)
        if err:
            for mid in batch:
                failed.append((mid, err))
            time.sleep(BATCH_SLEEP)
            continue

        payload = (data or {}).get("data") or {}
        errors = (data or {}).get("errors") or []
        err_map = {}
        for e in errors:
            path = e.get("path") or []
            if path:
                err_map[path[0]] = e.get("message", "unknown error")

        pending = []
        for i, mid in enumerate(batch):
            alias = f"m{i}"
            t = payload.get(alias)
            msg = err_map.get(alias, "")
            if t:
                parsed = parse_transcript(t)
                if parsed:
                    refetched[mid] = parsed
                else:
                    failed.append((mid, "parse failed"))
            elif "Too many requests" in msg or "rate" in msg.lower():
                pending.append((mid, msg))
            else:
                failed.append((mid, msg or "null transcript"))

        retry_idx = 0
        while pending and retry_idx < len(BACKOFFS):
            sleep_for = BACKOFFS[retry_idx]
            print(f"  {len(pending)} per-alias rate-limited; sleeping {sleep_for}s ...")
            time.sleep(sleep_for)
            retry_ids = [mid for mid, _ in pending]
            data2, err2 = fetch_batch(retry_ids)
            if err2:
                for mid, msg in pending:
                    failed.append((mid, f"retry {retry_idx + 1}: {err2}"))
                pending = []
                break
            payload2 = (data2 or {}).get("data") or {}
            errors2 = (data2 or {}).get("errors") or []
            emap2 = {}
            for e in errors2:
                p = e.get("path") or []
                if p:
                    emap2[p[0]] = e.get("message", "unknown error")
            new_pending = []
            for i, mid in enumerate(retry_ids):
                alias = f"m{i}"
                t = payload2.get(alias)
                msg = emap2.get(alias, "")
                if t:
                    parsed = parse_transcript(t)
                    if parsed:
                        refetched[mid] = parsed
                    else:
                        failed.append((mid, "parse failed"))
                elif "Too many requests" in msg or "rate" in msg.lower():
                    new_pending.append((mid, msg))
                else:
                    failed.append((mid, msg or "null transcript"))
            pending = new_pending
            retry_idx += 1

        for mid, msg in pending:
            failed.append((mid, f"rate-limit retries exhausted: {msg}"))

        time.sleep(BATCH_SLEEP)

    updated_rows = []
    updated_count = 0
    for r in rows:
        mid = r.get("meeting_id", "")
        if mid in refetched:
            new_r = refetched[mid]
            still_empty_link = not (new_r.get("meeting_link") or "").strip()
            still_empty_ext = not (new_r.get("external_attendees") or "").strip()
            if still_empty_link and still_empty_ext and not r.get("external_attendees"):
                updated_rows.append(r)
                failed.append((mid, "still empty after refetch"))
            else:
                updated_rows.append(new_r)
                updated_count += 1
        else:
            updated_rows.append(r)

    updated_rows.sort(key=lambda r: ((r.get("company") or "").lower(), r.get("date") or ""))

    with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(updated_rows)

    if failed:
        with open(FAIL_LOG, "w", encoding="utf-8") as f:
            for mid, reason in failed:
                f.write(f"{mid}\t{reason}\n")

    recovered = updated_count
    still_failed = starting_missing - recovered
    runtime = time.time() - t0

    print()
    print("==== Summary ====")
    print(f"Starting missing count: {starting_missing}")
    print(f"Recovered count:        {recovered}")
    print(f"Still-failed count:     {still_failed}")
    print(f"Total API calls:        {api_calls}")
    print(f"Total runtime:          {runtime:.1f}s")
    print(f"Output CSV:             {OUT_CSV}")
    if failed:
        print(f"Failed log:             {FAIL_LOG} ({len(failed)} entries)")


if __name__ == "__main__":
    main()
