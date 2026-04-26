import os
import json
import time
import requests
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("FIREFLIES_API_KEY")
if not api_key:
    print("ERROR: FIREFLIES_API_KEY not found in .env")
    exit(1)

url = "https://api.fireflies.ai/graphql"
headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json",
}

RAW_FILE = "meetings_raw.json"
OUTPUT_FILE = "spinbot_meetings.json"

# Step 1: Load cached meetings
print(f"Loading cached meetings from {RAW_FILE}...")
with open(RAW_FILE, "r", encoding="utf-8") as f:
    all_meetings = json.load(f)
print(f"Loaded {len(all_meetings)} meetings.")

# Step 2: Filter for Spinbot
spinbot_matches = [
    m for m in all_meetings
    if m.get("title") and "spinbot" in m["title"].lower()
]

print(f"\nFound {len(spinbot_matches)} Spinbot meetings:")
for m in spinbot_matches:
    print(f"  - {m['title']} (id: {m['id']})")


# Step 3: Fetch full transcripts
def fetch_full_transcript(meeting_id):
    query = {
        "query": """
        query($id: String!) {
          transcript(id: $id) {
            id
            title
            date
            duration
            host_email
            organizer_email
            transcript_url
            audio_url
            video_url
            meeting_attendees {
              displayName
              email
              phoneNumber
              name
            }
            summary {
              keywords
              action_items
              overview
              short_summary
              bullet_gist
            }
            sentences {
              index
              speaker_name
              speaker_id
              text
              raw_text
              start_time
              end_time
            }
          }
        }
        """,
        "variables": {"id": meeting_id},
    }
    resp = requests.post(url, json=query, headers=headers)
    data = resp.json()
    if "errors" in data:
        print(f"  Error fetching {meeting_id}: {data['errors']}")
        return None
    return data.get("data", {}).get("transcript")


print(f"\nFetching full transcript details for {len(spinbot_matches)} meetings...")
full_meetings = []
for i, m in enumerate(spinbot_matches):
    print(f"  [{i+1}/{len(spinbot_matches)}] {m['title']}...")
    full = fetch_full_transcript(m["id"])
    if full:
        full_meetings.append(full)
    if i < len(spinbot_matches) - 1:
        time.sleep(1)

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(full_meetings, f, indent=2)
print(f"\nSaved {len(full_meetings)} full Spinbot transcripts to {OUTPUT_FILE}")
