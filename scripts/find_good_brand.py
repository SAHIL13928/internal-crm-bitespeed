import json
import re
from datetime import datetime, timezone, timedelta

with open("meetings_raw.json", "r", encoding="utf-8") as f:
    meetings = json.load(f)

now = datetime.now(tz=timezone.utc)
cutoff = now - timedelta(days=90)

# Most titles follow "ClientName <> Bitespeed" pattern
brand_data = {}

for m in meetings:
    title = m.get("title", "")
    # Extract brand name from "Brand <> Bitespeed ..." pattern
    match = re.match(r"^(.+?)\s*<>\s*Bitespeed", title, re.IGNORECASE)
    if not match:
        match = re.match(r"^Bitespeed\s*<>\s*(.+?)(\s*\[|$)", title, re.IGNORECASE)
    if not match:
        continue

    brand = match.group(1).strip()
    meeting_date = datetime.fromtimestamp(m["date"] / 1000, tz=timezone.utc)

    external_emails = [
        a["email"] for a in m.get("meeting_attendees", [])
        if a.get("email") and not a["email"].lower().endswith("@bitespeed.co")
    ]

    if brand not in brand_data:
        brand_data[brand] = {
            "meetings": 0,
            "external_emails": set(),
            "has_recent": False,
            "dates": [],
        }

    bd = brand_data[brand]
    bd["meetings"] += 1
    bd["external_emails"].update(external_emails)
    bd["dates"].append(meeting_date)
    if meeting_date >= cutoff:
        bd["has_recent"] = True

# Filter: >=3 meetings, >=1 external email, has recent meeting
good = {
    k: v for k, v in brand_data.items()
    if v["meetings"] >= 3 and len(v["external_emails"]) >= 1 and v["has_recent"]
}

# Sort by meeting count descending
ranked = sorted(good.items(), key=lambda x: x[1]["meetings"], reverse=True)

print(f"Found {len(ranked)} brands matching criteria (>=3 meetings, >=1 external email, recent meeting)\n")
print(f"{'Brand':<35} {'Meetings':<10} {'Ext Emails':<12} {'Latest Meeting':<16} {'Sample External Emails'}")
print("-" * 130)
for brand, d in ranked[:30]:
    latest = max(d["dates"]).strftime("%Y-%m-%d")
    sample = ", ".join(list(d["external_emails"])[:3])
    if len(d["external_emails"]) > 3:
        sample += f" (+{len(d['external_emails'])-3} more)"
    print(f"{brand:<35} {d['meetings']:<10} {len(d['external_emails']):<12} {latest:<16} {sample}")
