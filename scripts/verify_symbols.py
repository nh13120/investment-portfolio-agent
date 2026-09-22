"""
Check every symbol in a CSV against EODHD, and suggest fixes for failures.

Run:  python scripts/verify_symbols.py scripts/sample_positions.csv

Improvement over v1: EODHD fails the WHOLE request if the symbol in the URL
path is invalid, which previously killed a batch of 20 and reported nothing
about any of them. Now a failed batch falls back to checking symbols one at a
time, so a single bad ticker can't hide 19 good ones. Failures are then run
through EODHD's search endpoint to suggest the correct symbol.
"""

import csv
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from dotenv import load_dotenv

load_dotenv()

KEY = os.environ.get("EODHD_API_KEY")
if not KEY:
    print("EODHD_API_KEY not set in .env")
    sys.exit(1)

path = sys.argv[1] if len(sys.argv) > 1 else "scripts/sample_positions.csv"
rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
symbols = [r["ticker"].strip().upper() for r in rows if r.get("ticker")]

print(f"\nChecking {len(symbols)} symbols against EODHD...\n")


def fetch_batch(batch: list[str]) -> dict | None:
    """Query a group of symbols. Returns None if the whole request failed."""
    first, rest = batch[0], batch[1:]
    params = {"api_token": KEY, "fmt": "json"}
    if rest:
        params["s"] = ",".join(rest)
    try:
        r = httpx.get(f"https://eodhd.com/api/real-time/{first}",
                      params=params, timeout=30)
        if r.status_code != 200:
            return None
        data = r.json()
    except Exception:
        return None

    out = {}
    for q in (data if isinstance(data, list) else [data]):
        if isinstance(q, dict) and q.get("code"):
            out[str(q["code"]).upper()] = q.get("close")
    return out


def search(sym: str) -> list[str]:
    """Ask EODHD what the correct symbol might be."""
    base = sym.rsplit(".", 1)[0]
    try:
        r = httpx.get(f"https://eodhd.com/api/search/{base}",
                      params={"api_token": KEY, "fmt": "json", "limit": 5},
                      timeout=20)
        if r.status_code != 200:
            return []
        results = r.json()
    except Exception:
        return []
    if not isinstance(results, list):
        return []
    return [
        f"{x.get('Code')}.{x.get('Exchange')}  ({str(x.get('Name', ''))[:40]})"
        for x in results[:5]
        if x.get("Code")
    ]


resolved, failed = {}, []

for i in range(0, len(symbols), 20):
    batch = symbols[i : i + 20]
    result = fetch_batch(batch)

    if result is None:
        # Batch died -- almost always one bad symbol in the URL path.
        # Check individually so the other 19 aren't lost.
        print(f"  batch {i}-{i + len(batch) - 1} failed, checking individually...")
        for s in batch:
            one = fetch_batch([s])
            if one and one.get(s) not in (None, "NA"):
                resolved[s] = one[s]
            else:
                failed.append(s)
            time.sleep(0.05)
        continue

    for s in batch:
        price = result.get(s)
        if price in (None, "NA"):
            failed.append(s)
        else:
            resolved[s] = price

print(f"\nRESOLVED ({len(resolved)}/{len(symbols)})")

if failed:
    print(f"\nNOT RESOLVED ({len(failed)}):\n")
    for s in failed:
        print(f"  {s}")
        for suggestion in search(s):
            print(f"      try: {suggestion}")
        print()
    print("Edit those rows in the CSV, then run this again.")
    print("If a holding genuinely isn't covered, delete the row -- otherwise")
    print("the bot reports a portfolio total that silently excludes it.\n")
else:
    print("\nAll symbols resolved. Safe to import.\n")

sys.exit(0 if not failed else 1)