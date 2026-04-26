import json
import csv
import re
from datetime import datetime, timezone

with open("meetings_with_links.json", "r", encoding="utf-8") as f:
    meetings = json.load(f)

OUTPUT_CSV = "all_meet_links_organized.csv"

# Build rows grouped by company
rows = []
for m in meetings:
    title = m.get("title", "")
    meeting_id = m.get("id", "")
    meeting_link = m.get("meeting_link") or ""

    # Date with time
    date_ms = m.get("date")
    date_str = ""
    if date_ms:
        dt = datetime.fromtimestamp(date_ms / 1000, tz=timezone.utc)
        date_str = dt.strftime("%Y-%m-%d %H:%M")

    # Extract company name from title
    company = ""
    match = re.match(r"^(.+?)\s*<>\s*[Bb]ite[Ss]peed", title)
    if not match:
        match = re.match(r"^[Bb]ite[Ss]peed\s*<>\s*(.+?)(\s*\[|\s*\||$)", title)
    if not match:
        match = re.match(r"^(.+?)\s*<>\s*", title)
    if match:
        company = match.group(1).strip()

    attendees = m.get("meeting_attendees", [])
    external_emails = []
    internal_emails = []
    for att in attendees:
        email = att.get("email") or ""
        name = att.get("name") or att.get("displayName") or ""
        if email.lower().endswith("@bitespeed.co"):
            internal_emails.append(email)
        elif email:
            external_emails.append(f"{name} <{email}>" if name else email)

    rows.append({
        "company": company,
        "meeting_title": title,
        "date": date_str,
        "meeting_link": meeting_link,
        "external_attendees": "; ".join(external_emails),
        "internal_attendees": "; ".join(internal_emails),
        "meeting_id": meeting_id,
    })

# Sort by company name, then date descending
rows.sort(key=lambda r: (r["company"].lower(), r["date"]), reverse=False)

with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=[
        "company", "date", "meeting_title", "meeting_link",
        "external_attendees", "internal_attendees", "meeting_id"
    ])
    writer.writeheader()
    writer.writerows(rows)

# Stats
companies = set(r["company"] for r in rows if r["company"])
ext_emails = set()
for r in rows:
    for part in r["external_attendees"].split("; "):
        # extract email from "name <email>" or plain email
        match = re.search(r"<(.+?)>", part)
        if match:
            ext_emails.add(match.group(1))
        elif "@" in part:
            ext_emails.add(part.strip())

print(f"CSV saved to {OUTPUT_CSV}")
print(f"  {len(rows)} meetings")
print(f"  {len(companies)} unique companies")
print(f"  {len(ext_emails)} unique external emails")
print(f"  Sorted by company name, then date")
print(f"  Columns: company | date | meeting_title | meeting_link | external_attendees | internal_attendees | meeting_id")
