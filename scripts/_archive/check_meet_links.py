import os
import json
import time
import random
import requests
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("FIREFLIES_API_KEY")
url = "https://api.fireflies.ai/graphql"
headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json",
}

# Load all meetings
with open("meetings_raw.json", "r", encoding="utf-8") as f:
    all_meetings = json.load(f)

# Group by brand (pick one meeting per brand to test)
import re
brands = {}
for m in all_meetings:
    title = m.get("title", "")
    match = re.match(r"^(.+?)\s*<>\s*Bitespeed", title, re.IGNORECASE)
    if not match:
        match = re.match(r"^Bitespeed\s*<>\s*(.+?)(\s*\[|$)", title, re.IGNORECASE)
    if not match:
        continue
    brand = match.group(1).strip()
    if brand not in brands:
        brands[brand] = []
    brands[brand].append(m)

# Pick 50 random brands
brand_names = list(brands.keys())
random.seed(42)
sample = random.sample(brand_names, min(50, len(brand_names)))

print(f"Testing meet_link for 50 random brands (1 meeting each)...\n")

has_link = 0
no_link = 0
results = []

for i, brand in enumerate(sample):
    meeting = brands[brand][0]  # first meeting for this brand
    mid = meeting["id"]

    query = {
        "query": """
        query($id: String!) {
          transcript(id: $id) {
            id
            title
            meeting_link
          }
        }
        """,
        "variables": {"id": mid},
    }

    resp = requests.post(url, json=query, headers=headers)
    data = resp.json()
    transcript = data.get("data", {}).get("transcript", {})
    link = transcript.get("meeting_link") if transcript else None

    status = "YES" if link else "NO"
    if link:
        has_link += 1
    else:
        no_link += 1

    results.append({"brand": brand, "meeting_id": mid, "meeting_link": link or ""})
    print(f"  [{i+1:2d}/50] {status:3s}  {brand:<35} {link or '(none)'}")

    if i < len(sample) - 1:
        time.sleep(0.5)

print(f"\n{'='*60}")
print(f"SUMMARY: {has_link} with meet link, {no_link} without meet link (out of 50)")
print(f"{'='*60}")

# Save results
with open("meet_link_check.json", "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2)
print(f"\nSaved details to meet_link_check.json")
