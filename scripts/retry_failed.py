import csv
import time
from refetch_missing import (
    fetch_batch, parse_transcript, BATCH_SIZE, BATCH_SLEEP, BACKOFFS,
)

SRC_CSV = "all_meet_links_organized_v2.csv"
FAIL_LOG = "failed_meetings.log"
OUT_CSV = "all_meet_links_organized_v2.csv"
NEW_FAIL_LOG = "failed_meetings_pass2.log"


def main():
    t0 = time.time()

    with open(FAIL_LOG, "r", encoding="utf-8") as f:
        ids = []
        for line in f:
            parts = line.strip().split("\t")
            if parts and parts[0]:
                ids.append(parts[0])
    ids = list(dict.fromkeys(ids))
    print(f"Retrying {len(ids)} failed ids")

    with open(SRC_CSV, "r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    fieldnames = [
        "company", "date", "meeting_title", "meeting_link",
        "external_attendees", "internal_attendees", "meeting_id",
    ]

    refetched = {}
    still_failed = []

    batches = [ids[i:i + BATCH_SIZE] for i in range(0, len(ids), BATCH_SIZE)]
    print(f"Will issue {len(batches)} batched calls (longer sleeps)")

    INTER_BATCH = 5

    for bi, batch in enumerate(batches, 1):
        if bi % 10 == 0 or bi == 1:
            print(f"Batch {bi}/{len(batches)}")
        data, err = fetch_batch(batch)
        if err:
            for mid in batch:
                still_failed.append((mid, err))
            time.sleep(INTER_BATCH)
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
                    still_failed.append((mid, "parse failed"))
            elif "Too many requests" in msg or "rate" in msg.lower():
                pending.append((mid, msg))
            else:
                still_failed.append((mid, msg or "null transcript"))

        retry_idx = 0
        while pending and retry_idx < len(BACKOFFS):
            sleep_for = BACKOFFS[retry_idx]
            print(f"  {len(pending)} per-alias rate-limited; sleeping {sleep_for}s ...")
            time.sleep(sleep_for)
            retry_ids = [mid for mid, _ in pending]
            data2, err2 = fetch_batch(retry_ids)
            if err2:
                for mid, _ in pending:
                    still_failed.append((mid, f"retry {retry_idx + 1}: {err2}"))
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
                        still_failed.append((mid, "parse failed"))
                elif "Too many requests" in msg or "rate" in msg.lower():
                    new_pending.append((mid, msg))
                else:
                    still_failed.append((mid, msg or "null transcript"))
            pending = new_pending
            retry_idx += 1

        for mid, msg in pending:
            still_failed.append((mid, f"rate-limit retries exhausted: {msg}"))

        time.sleep(INTER_BATCH)

    updated_rows = []
    updated_count = 0
    for r in rows:
        mid = r.get("meeting_id", "")
        if mid in refetched:
            new_r = refetched[mid]
            if not (new_r.get("external_attendees") or "").strip() and not (new_r.get("meeting_link") or "").strip():
                updated_rows.append(r)
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

    if still_failed:
        with open(NEW_FAIL_LOG, "w", encoding="utf-8") as f:
            for mid, reason in still_failed:
                f.write(f"{mid}\t{reason}\n")

    print()
    print("==== Retry Pass Summary ====")
    print(f"Input failed ids:    {len(ids)}")
    print(f"Recovered this pass: {updated_count}")
    print(f"Still failed:        {len(still_failed)}")
    print(f"Runtime:             {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
