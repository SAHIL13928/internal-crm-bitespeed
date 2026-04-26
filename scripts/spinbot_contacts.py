import json
import csv
from datetime import datetime, timezone

INPUT_FILE = "spinbot_meetings.json"
OUTPUT_CSV = "spinbot_contacts.csv"

with open(INPUT_FILE, "r", encoding="utf-8") as f:
    meetings = json.load(f)

contacts = {}

for meeting in meetings:
    meeting_id = meeting["id"]
    meeting_date = datetime.fromtimestamp(meeting["date"] / 1000, tz=timezone.utc)

    for att in meeting.get("meeting_attendees", []):
        email = att.get("email")
        if not email:
            continue
        if email.lower().endswith("@bitespeed.co"):
            continue

        name = att.get("name") or att.get("displayName") or ""
        phone = att.get("phoneNumber") or ""

        if email not in contacts:
            contacts[email] = {
                "email": email,
                "name": name,
                "phone_number": phone,
                "email_domain": email.split("@")[1] if "@" in email else "",
                "meetings_count": 0,
                "first_meeting_date": meeting_date,
                "last_meeting_date": meeting_date,
                "meeting_ids": [],
            }

        c = contacts[email]
        c["meetings_count"] += 1
        c["meeting_ids"].append(meeting_id)
        if meeting_date < c["first_meeting_date"]:
            c["first_meeting_date"] = meeting_date
        if meeting_date > c["last_meeting_date"]:
            c["last_meeting_date"] = meeting_date
        if not c["name"] and name:
            c["name"] = name
        if not c["phone_number"] and phone:
            c["phone_number"] = phone

contact_list = sorted(contacts.values(), key=lambda c: (-c["meetings_count"], c["email"]))

# Print table
print(f"\n{'Email':<40} {'Name':<25} {'Phone':<18} {'Domain':<25} {'Meetings':<10} {'First':<14} {'Last':<14}")
print("-" * 150)
for c in contact_list:
    print(
        f"{c['email']:<40} "
        f"{c['name']:<25} "
        f"{c['phone_number']:<18} "
        f"{c['email_domain']:<25} "
        f"{c['meetings_count']:<10} "
        f"{c['first_meeting_date'].strftime('%Y-%m-%d'):<14} "
        f"{c['last_meeting_date'].strftime('%Y-%m-%d'):<14}"
    )

# Save CSV
with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["email", "name", "phone_number", "email_domain", "meetings_count", "first_meeting_date", "last_meeting_date", "meeting_ids"])
    for c in contact_list:
        writer.writerow([
            c["email"],
            c["name"],
            c["phone_number"],
            c["email_domain"],
            c["meetings_count"],
            c["first_meeting_date"].strftime("%Y-%m-%d"),
            c["last_meeting_date"].strftime("%Y-%m-%d"),
            ";".join(c["meeting_ids"]),
        ])

print(f"\n{len(contact_list)} unique external contacts found across {len(meetings)} meetings.")
print(f"Saved to {OUTPUT_CSV}")
