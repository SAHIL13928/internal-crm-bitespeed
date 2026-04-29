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
OUTPUT_FILE = "biobasket_meetings.json"


def fetch_all_meetings():
    """Fetch all meetings paginated (limit=50 per page)."""
    all_transcripts = []
    skip = 0
    limit = 50
    page = 1

    while True:
        print(f"Fetching page {page} (skip={skip}, limit={limit})...")
        query = {
            "query": """
            query($limit: Int, $skip: Int) {
              transcripts(limit: $limit, skip: $skip) {
                id
                title
                date
                duration
                host_email
                organizer_email
                transcript_url
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
                }
              }
            }
            """,
            "variables": {"limit": limit, "skip": skip},
        }

        resp = requests.post(url, json=query, headers=headers)
        data = resp.json()

        if "errors" in data:
            print(f"API error on page {page}: {data['errors']}")
            break

        transcripts = data.get("data", {}).get("transcripts", [])
        if not transcripts:
            print(f"No more results. Stopped at page {page}.")
            break

        all_transcripts.extend(transcripts)
        print(f"  Got {len(transcripts)} meetings (total so far: {len(all_transcripts)})")

        if len(transcripts) < limit:
            print("Last page reached.")
            break

        skip += limit
        page += 1
        time.sleep(1)

    return all_transcripts


def fetch_full_transcript(meeting_id):
    """Fetch full transcript details including sentences for a single meeting."""
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
        print(f"  Error fetching transcript {meeting_id}: {data['errors']}")
        return None

    return data.get("data", {}).get("transcript")


# Step 1: Load or fetch all meetings
if os.path.exists(RAW_FILE):
    print(f"Loading cached meetings from {RAW_FILE}...")
    with open(RAW_FILE, "r", encoding="utf-8") as f:
        all_meetings = json.load(f)
    print(f"Loaded {len(all_meetings)} meetings from cache.")
else:
    print("No cached data found. Fetching all meetings from Fireflies...")
    all_meetings = fetch_all_meetings()
    with open(RAW_FILE, "w", encoding="utf-8") as f:
        json.dump(all_meetings, f, indent=2)
    print(f"\nSaved {len(all_meetings)} meetings to {RAW_FILE}")

# Step 2: Filter for Biobasket
biobasket_matches = [
    m for m in all_meetings
    if m.get("title") and ("biobasket" in m["title"].lower() or "bio basket" in m["title"].lower())
]

print(f"\nFound {len(biobasket_matches)} Biobasket meetings:")
for m in biobasket_matches:
    print(f"  - {m['title']} (id: {m['id']})")

# Step 3: Fetch full transcripts for each match
if biobasket_matches:
    print(f"\nFetching full transcript details for {len(biobasket_matches)} meetings...")
    full_meetings = []
    for i, m in enumerate(biobasket_matches):
        print(f"  [{i+1}/{len(biobasket_matches)}] {m['title']}...")
        full = fetch_full_transcript(m["id"])
        if full:
            full_meetings.append(full)
        if i < len(biobasket_matches) - 1:
            time.sleep(1)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(full_meetings, f, indent=2)
    print(f"\nSaved {len(full_meetings)} full Biobasket transcripts to {OUTPUT_FILE}")
else:
    print("\nNo Biobasket meetings found. Nothing to fetch.")
