"""Smoke test the CRM backend against 50 real merchants.

Picks 50 from `ShopUrl <> Brand Name` sheet — biased toward ones that have
meetings/contacts mapped — and walks the same endpoint sequence the
front-end mockup would call. Prints a per-merchant coverage line plus an
aggregate summary at the end.
"""
import os
import random
import sys
import time

import openpyxl
import requests

sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

BASE = os.environ.get("CRM_BASE", "http://127.0.0.1:8765")
SEED = 7
random.seed(SEED)

# 1. pull brand → shop pairs
_XLSX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data", "scratch", "Fireflies Mapping (1) - ENRICHED.xlsx")
wb = openpyxl.load_workbook(_XLSX, data_only=True)
ws = wb["ShopUrl <> Brand Name"]
all_pairs = []
for i, row in enumerate(ws.iter_rows(values_only=True)):
    if i == 0:
        continue
    name, shop = row[0], row[1]
    if name and shop and isinstance(shop, str):
        all_pairs.append((str(name).strip(), shop.strip().lower()))

# 2. ask the API which of those exist + score richness
candidates = []
for name, shop in all_pairs:
    r = requests.get(f"{BASE}/api/merchants/{shop}")
    if r.status_code != 200:
        continue
    p = r.json()
    score = (
        len(p.get("contacts", [])) * 1
        + len(p.get("whatsapp_groups", [])) * 2
        + p["kpi"]["meetings_7d"] * 5
        + (5 if any(c.get("phone") for c in p.get("contacts", [])) else 0)
    )
    candidates.append((score, name, shop, p))

# Pick 50: top 30 by richness + 20 random others (so we stress the long tail)
candidates.sort(key=lambda x: -x[0])
top = candidates[:30]
rest = candidates[30:]
random.shuffle(rest)
sample = top + rest[:20]

print(f"\nTesting {len(sample)} merchants (of {len(all_pairs)} known brand pairs, "
      f"{len(candidates)} live in DB)\n")

cols = ["#", "Brand", "shopUrl", "Phon", "Mail", "WA", "Meet", "Sum", "Tline", "ms"]
print(f"{cols[0]:>3} {cols[1]:<28} {cols[2]:<38} "
      f"{cols[3]:>4} {cols[4]:>4} {cols[5]:>3} {cols[6]:>4} {cols[7]:>3} {cols[8]:>5} {cols[9]:>5}")
print("-" * 110)

agg = dict(
    n=0, with_phone=0, with_email=0, with_wa=0,
    with_meeting=0, with_summary=0, with_action_items=0,
    timeline_items=0,
)

for i, (_, brand, shop, profile) in enumerate(sample, 1):
    t0 = time.perf_counter()
    contacts = profile.get("contacts", [])
    phones = sum(1 for c in contacts if c.get("phone"))
    emails = sum(1 for c in contacts if c.get("email"))
    wa = len(profile.get("whatsapp_groups", []))

    # meetings list + sample one detail
    meetings = requests.get(f"{BASE}/api/merchants/{shop}/meetings?limit=5").json()
    has_meeting = len(meetings) > 0
    has_summary = 0
    has_action = 0
    if meetings:
        d = requests.get(f"{BASE}/api/meetings/{meetings[0]['id']}").json()
        has_summary = 1 if d.get("summary_overview") or d.get("summary_short") else 0
        has_action = 1 if d.get("action_items") else 0

    # timeline
    tl = requests.get(f"{BASE}/api/merchants/{shop}/timeline?limit=20").json()
    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    agg["n"] += 1
    agg["with_phone"] += 1 if phones else 0
    agg["with_email"] += 1 if emails else 0
    agg["with_wa"] += 1 if wa else 0
    agg["with_meeting"] += 1 if has_meeting else 0
    agg["with_summary"] += has_summary
    agg["with_action_items"] += has_action
    agg["timeline_items"] += len(tl)

    brand_short = (brand[:26] + "..") if len(brand) > 28 else brand
    shop_short = (shop[:36] + "..") if len(shop) > 38 else shop
    print(f"{i:>3} {brand_short:<28} {shop_short:<38} "
          f"{phones:>4} {emails:>4} {wa:>3} {len(meetings):>4} "
          f"{('Y' if has_summary else '-'):>3} {len(tl):>5} {elapsed_ms:>5}")

n = agg["n"] or 1
print()
print("=" * 64)
print("AGGREGATE COVERAGE (out of", n, "merchants)")
print("=" * 64)
print(f"  has any phone number:     {agg['with_phone']:>3}/{n}  ({agg['with_phone']*100//n}%)")
print(f"  has any email:            {agg['with_email']:>3}/{n}  ({agg['with_email']*100//n}%)")
print(f"  has WhatsApp group(s):    {agg['with_wa']:>3}/{n}  ({agg['with_wa']*100//n}%)")
print(f"  has at least 1 meeting:   {agg['with_meeting']:>3}/{n}  ({agg['with_meeting']*100//n}%)")
print(f"  meeting has AI summary:   {agg['with_summary']:>3}/{n}  ({agg['with_summary']*100//n}%)")
print(f"  meeting has action items: {agg['with_action_items']:>3}/{n}  ({agg['with_action_items']*100//n}%)")
print(f"  avg timeline items / shop: {agg['timeline_items']/n:.1f}")
