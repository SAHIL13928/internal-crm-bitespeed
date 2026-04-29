import os
import json
import csv
import time
import requests
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("FIREFLIES_API_KEY")
url = "https://api.fireflies.ai/graphql"
headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json",
}

RAW_FILE = "meetings_with_links.json"
OUTPUT_CSV = "all_meet_links.csv"

# Step 1: Fetch all meetings with meeting_link
all_meetings = []
skip = 0
limit = 50
page = 1

while True:
    print(f"Fetching page {page} (skip={skip})...")
    query = {
        "query": """
        query($limit: Int, $skip: Int) {
          transcripts(limit: $limit, skip: $skip) {
            id
            title
            date
            meeting_link
            meeting_attendees {
              displayName
              email
              name
            }
          }
        }
        """,
        "variables": {"limit": limit, "skip": skip},
    }

    resp = requests.post(url, json=query, headers=headers)
    data = resp.json()

    if "errors" in data:
        print(f"API error on page {page}: {data['errors'][0].get('message', data['errors'])}")
        break

    transcripts = data.get("data", {}).get("transcripts", [])
    if not transcripts:
        print(f"No more results at page {page}.")
        break

    all_meetings.extend(transcripts)
    print(f"  Got {len(transcripts)} (total: {len(all_meetings)})")

    if len(transcripts) < limit:
        break

    skip += limit
    page += 1
    time.sleep(1)

# Save raw
with open(RAW_FILE, "w", encoding="utf-8") as f:
    json.dump(all_meetings, f, indent=2)
print(f"\nFetched {len(all_meetings)} meetings. Saved to {RAW_FILE}")

# Step 2: Build CSV — one row per attendee per meeting
rows = []
no_link_count = 0
for m in all_meetings:
    meeting_link = m.get("meeting_link") or ""
    if not meeting_link:
        no_link_count += 1

    title = m.get("title", "")
    meeting_id = m.get("id", "")
    date_ms = m.get("date")
    date_str = ""
    if date_ms:
        from datetime import datetime, timezone
        date_str = datetime.fromtimestamp(date_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")

    attendees = m.get("meeting_attendees", [])
    if not attendees:
        # Still record the meeting even with no attendees
        rows.append({
            "meeting_id": meeting_id,
            "title": title,
            "date": date_str,
            "meeting_link": meeting_link,
            "attendee_email": "",
            "attendee_name": "",
        })
    else:
        for att in attendees:
            email = att.get("email") or ""
            name = att.get("name") or att.get("displayName") or ""
            rows.append({
                "meeting_id": meeting_id,
                "title": title,
                "date": date_str,
                "meeting_link": meeting_link,
                "attendee_email": email,
                "attendee_name": name,
            })

# Write CSV
with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=["meeting_id", "title", "date", "meeting_link", "attendee_email", "attendee_name"])
    writer.writeheader()
    writer.writerows(rows)

unique_meetings = len(all_meetings)
unique_emails = len(set(r["attendee_email"] for r in rows if r["attendee_email"]))
print(f"\nCSV saved to {OUTPUT_CSV}")
print(f"  {unique_meetings} meetings")
print(f"  {len(rows)} total rows (one per attendee per meeting)")
print(f"  {unique_emails} unique email addresses")
print(f"  {no_link_count} meetings without a meet link")
