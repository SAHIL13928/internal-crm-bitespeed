import os
import json
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

query = {
    "query": """
    query {
      transcripts(limit: 3) {
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
          location
        }
        summary {
          keywords
          action_items
          overview
          short_summary
          bullet_gist
        }
      }
    }
    """
}

response = requests.post(url, json=query, headers=headers)
data = response.json()

if "errors" in data:
    print("--- ERRORS ---")
    for error in data["errors"]:
        print(f"  {error.get('message', error)}")
else:
    output = json.dumps(data, indent=2)
    print(output)
    with open("sample_meetings.json", "w", encoding="utf-8") as f:
        f.write(output)
    print(f"\nSaved to sample_meetings.json")
