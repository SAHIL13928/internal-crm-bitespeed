import csv
import re
from collections import defaultdict

ARINDAM_CSV = "meetlinkstoshopUrl (1).csv"
SCRAPE_CSV = "all_meet_links_organized.csv"
OUT_EMAILS = "emails_to_clients.csv"
OUT_UNMATCHED_MEETINGS = "meetings_without_shopurl.csv"
OUT_ORPHAN_EMAILS = "emails_without_client.csv"
OUT_CONFLICTS = "conflicts.csv"

MEET_RE = re.compile(r"https://meet\.google\.com/[a-z0-9\-]+", re.IGNORECASE)

# ── Step 1: Build meet_link → (shopUrl, group_name) lookup from Arindam's CSV ──

# First pass: count (meet_link, shopUrl) occurrences to handle conflicts
link_shop_counts = defaultdict(lambda: defaultdict(int))  # meet_link -> {shopUrl: count}
link_group = {}  # meet_link -> group_name (from most frequent shopUrl)

print("Reading Arindam's CSV...")
with open(ARINDAM_CSV, "r", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    arindam_rows = 0
    for row in reader:
        arindam_rows += 1
        content = row.get("content", "")
        shop_url = row.get("shopUrl", "").strip()
        group_name = row.get("group_name", "").strip()
        if not shop_url:
            continue
        links = MEET_RE.findall(content)
        for link in links:
            link = link.lower()
            link_shop_counts[link][shop_url] += 1
            link_group[link] = group_name

print(f"  {arindam_rows} rows read, {len(link_shop_counts)} unique meet links extracted")

# Resolve conflicts: keep most frequent shopUrl per meet_link
meet_to_shop = {}
conflicts = []
for link, shop_counts in link_shop_counts.items():
    if len(shop_counts) > 1:
        sorted_shops = sorted(shop_counts.items(), key=lambda x: -x[1])
        winner = sorted_shops[0][0]
        meet_to_shop[link] = winner
        for shop, count in sorted_shops:
            conflicts.append({
                "meet_link": link,
                "shopUrl": shop,
                "count": count,
                "chosen": "YES" if shop == winner else "NO",
            })
    else:
        meet_to_shop[link] = list(shop_counts.keys())[0]

# Write conflicts
if conflicts:
    with open(OUT_CONFLICTS, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["meet_link", "shopUrl", "count", "chosen"])
        writer.writeheader()
        writer.writerows(conflicts)

# Also build meet_link -> group_name using the winning shopUrl's group
meet_to_group = {}
with open(ARINDAM_CSV, "r", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        content = row.get("content", "")
        shop_url = row.get("shopUrl", "").strip()
        group_name = row.get("group_name", "").strip()
        links = MEET_RE.findall(content)
        for link in links:
            link = link.lower()
            if meet_to_shop.get(link) == shop_url:
                meet_to_group[link] = group_name

# ── Step 2: Read scrape CSV and join ──

print("Reading scrape CSV...")
# Track per (email, shopUrl)
email_shop_data = defaultdict(lambda: {
    "meeting_count": 0,
    "dates": [],
    "group_name": "",
})
unmatched_meetings = []
matched_count = 0
total_meetings = 0

with open(SCRAPE_CSV, "r", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        total_meetings += 1
        meeting_link = (row.get("meeting_link") or "").strip().lower()
        ext_attendees_raw = row.get("external_attendees", "").strip()
        date = row.get("date", "")

        shop_url = meet_to_shop.get(meeting_link)

        if not shop_url:
            unmatched_meetings.append(row)
            continue

        matched_count += 1
        group_name = meet_to_group.get(meeting_link, "")

        # Explode external attendees by semicolon (our CSV uses ; separator)
        if not ext_attendees_raw:
            continue

        for part in ext_attendees_raw.split("; "):
            part = part.strip()
            if not part:
                continue
            # Handle "name <email>" or plain email
            email_match = re.search(r"<(.+?)>", part)
            if email_match:
                email = email_match.group(1).strip().lower()
            elif "@" in part:
                email = part.strip().lower()
            else:
                continue

            key = (email, shop_url)
            d = email_shop_data[key]
            d["meeting_count"] += 1
            d["group_name"] = group_name
            if date:
                d["dates"].append(date)

# ── Step 3: Output emails_to_clients.csv ──

email_rows = []
for (email, shop_url), d in email_shop_data.items():
    dates_sorted = sorted(d["dates"]) if d["dates"] else ["", ""]
    email_rows.append({
        "email": email,
        "shopUrl": shop_url,
        "group_name": d["group_name"],
        "meeting_count": d["meeting_count"],
        "first_meeting_date": dates_sorted[0] if dates_sorted else "",
        "last_meeting_date": dates_sorted[-1] if dates_sorted else "",
    })

email_rows.sort(key=lambda r: (r["shopUrl"], r["email"]))

with open(OUT_EMAILS, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=[
        "email", "shopUrl", "group_name", "meeting_count",
        "first_meeting_date", "last_meeting_date"
    ])
    writer.writeheader()
    writer.writerows(email_rows)

# ── Step 4: Output meetings_without_shopurl.csv ──

with open(OUT_UNMATCHED_MEETINGS, "w", newline="", encoding="utf-8") as f:
    if unmatched_meetings:
        writer = csv.DictWriter(f, fieldnames=unmatched_meetings[0].keys())
        writer.writeheader()
        writer.writerows(unmatched_meetings)

# ── Step 5: Output emails_without_client.csv (emails in matched meetings but shopUrl is empty-ish) ──
# These are external emails that appeared ONLY in unmatched meetings

matched_emails = set(email for (email, _) in email_shop_data.keys())

orphan_emails = defaultdict(lambda: {"meeting_count": 0, "dates": []})
with open(SCRAPE_CSV, "r", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        meeting_link = (row.get("meeting_link") or "").strip().lower()
        if meeting_link in meet_to_shop:
            continue  # this meeting was matched, skip
        ext_attendees_raw = row.get("external_attendees", "").strip()
        date = row.get("date", "")
        if not ext_attendees_raw:
            continue
        for part in ext_attendees_raw.split("; "):
            part = part.strip()
            email_match = re.search(r"<(.+?)>", part)
            if email_match:
                email = email_match.group(1).strip().lower()
            elif "@" in part:
                email = part.strip().lower()
            else:
                continue
            if email not in matched_emails:
                orphan_emails[email]["meeting_count"] += 1
                if date:
                    orphan_emails[email]["dates"].append(date)

orphan_rows = []
for email, d in sorted(orphan_emails.items()):
    dates_sorted = sorted(d["dates"]) if d["dates"] else []
    orphan_rows.append({
        "email": email,
        "meeting_count": d["meeting_count"],
        "first_meeting_date": dates_sorted[0] if dates_sorted else "",
        "last_meeting_date": dates_sorted[-1] if dates_sorted else "",
    })

with open(OUT_ORPHAN_EMAILS, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=["email", "meeting_count", "first_meeting_date", "last_meeting_date"])
    writer.writeheader()
    writer.writerows(orphan_rows)

# ── Summary ──

coverage = (matched_count / total_meetings * 100) if total_meetings else 0

print(f"\n{'='*60}")
print(f"SUMMARY")
print(f"{'='*60}")
print(f"  Total meetings in scrape:          {total_meetings}")
print(f"  Meetings matched to shopUrl:       {matched_count} ({coverage:.1f}%)")
print(f"  Meetings without shopUrl:          {len(unmatched_meetings)}")
print(f"  Unique meet links in Arindam CSV:  {len(meet_to_shop)}")
print(f"  Conflicts (multi shopUrl):         {len(set(c['meet_link'] for c in conflicts))}")
print(f"  Unique (email, shopUrl) mappings:  {len(email_rows)}")
print(f"  Orphan emails (no client):         {len(orphan_rows)}")
print(f"\n  Top 20 shopUrls by email count:")
print(f"  {'shopUrl':<45} {'Emails':<8} {'Group'}")
print(f"  {'-'*90}")

shop_email_count = defaultdict(lambda: {"count": 0, "group": ""})
for r in email_rows:
    shop_email_count[r["shopUrl"]]["count"] += 1
    shop_email_count[r["shopUrl"]]["group"] = r["group_name"]

for shop, d in sorted(shop_email_count.items(), key=lambda x: -x[1]["count"])[:20]:
    print(f"  {shop:<45} {d['count']:<8} {d['group']}")

print(f"\nOutputs:")
print(f"  {OUT_EMAILS}")
print(f"  {OUT_UNMATCHED_MEETINGS}")
print(f"  {OUT_ORPHAN_EMAILS}")
if conflicts:
    print(f"  {OUT_CONFLICTS}")
