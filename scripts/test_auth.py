import os
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
query = {"query": "{ user { name email } }"}

response = requests.post(url, json=query, headers=headers)

print(f"Status code: {response.status_code}")
print(f"Response: {response.json()}")

data = response.json()
if "errors" in data:
    print("\n--- ERRORS ---")
    for error in data["errors"]:
        print(f"  {error.get('message', error)}")
