import json
import csv
from datetime import datetime, timezone

INPUT_FILE = "biobasket_meetings.json"
OUTPUT_CSV = "biobasket_contacts.csv"

with open(INPUT_FILE, "r", encoding="utf-8") as f:
    meetings = json.load(f)

# Collect contacts keyed by email
contacts = {}

for meeting in meetings:
    meeting_id = meeting["id"]
    # date is epoch ms
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
        # Update name/phone if we got a better value
        if not c["name"] and name:
            c["name"] = name
        if not c["phone_number"] and phone:
            c["phone_number"] = phone

contact_list = sorted(contacts.values(), key=lambda c: c["email"])

# Print table
print(f"\n{'Email':<40} {'Name':<25} {'Phone':<18} {'Domain':<25} {'Meetings':<10} {'First Meeting':<14} {'Last Meeting':<14}")
print("-" * 160)
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

# Summary
print(f"\n{len(contact_list)} unique external contacts found across {len(meetings)} meetings.")
if len(contact_list) == 0:
    print("\nNOTE: No external attendee emails found. The Biobasket contact likely joined")
    print("without a tracked email. Check the transcript 'sentences' field — speakers are")
    print("labeled (e.g. 'Bio Basket') which may help identify the contact manually.")
    # Show unique speaker names as a fallback
    speakers = set()
    for meeting in meetings:
        for s in meeting.get("sentences", []):
            speakers.add(s.get("speaker_name", ""))
    internal_names = set()
    for meeting in meetings:
        for att in meeting.get("meeting_attendees", []):
            email = att.get("email", "")
            if email.endswith("@bitespeed.co"):
                # speaker names from internal folks won't help
                pass
    print(f"\nSpeakers found in transcripts: {', '.join(sorted(speakers))}")

print(f"\nSaved to {OUTPUT_CSV}")
