"""
Checks your setup piece by piece and tells you exactly what's wrong.

Run:  python scripts/check_setup.py

Each check runs independently, so you get a full picture rather than stopping
at the first problem. Work down the list fixing whatever shows [FAIL].
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

OK = "[ OK ]"
FAIL = "[FAIL]"

results = []


def check(label: str, passed: bool, hint: str = "") -> bool:
    print(f"{OK if passed else FAIL}  {label}")
    if not passed and hint:
        print(f"        -> {hint}")
    results.append(passed)
    return passed


print("\n--- Checking your setup ---\n")

# --- 1. Are the keys present? ---------------------------------------------
keys = {
    "TELEGRAM_TOKEN": "From @BotFather on Telegram (README Step 2)",
    "ANTHROPIC_API_KEY": "From console.anthropic.com (README Step 4)",
    "SUPABASE_URL": "Supabase > Project Settings > API (README Step 3)",
    "SUPABASE_SERVICE_KEY": "Supabase > Project Settings > API, 'service_role'",
    "EODHD_API_KEY": "From eodhd.com dashboard (README Step 4)",
}

all_keys_present = True
for name, hint in keys.items():
    value = os.environ.get(name, "")
    # Catch the classic mistake: saved the file with placeholder text still in it
    placeholder = value.startswith("x") or "xxxx" in value or not value
    if not check(f"{name} is set", not placeholder, hint):
        all_keys_present = False

if not all_keys_present:
    print("\nFill in the missing keys in your .env file, then run this again.\n")
    sys.exit(1)

print()

# --- 2. Can we reach Telegram? --------------------------------------------
try:
    import httpx

    token = os.environ["TELEGRAM_TOKEN"]
    r = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=15)
    data = r.json()
    if data.get("ok"):
        name = data["result"]["username"]
        check(f"Telegram bot reachable (@{name})", True)
        print(f"        -> Message @{name} on Telegram once setup is done")
    else:
        check("Telegram bot reachable", False,
              "Token rejected. Re-copy it from @BotFather.")
except Exception as e:
    check("Telegram bot reachable", False, f"{e}")

# --- 3. Can we reach Supabase and do the tables exist? --------------------
try:
    from supabase import create_client

    sb = create_client(os.environ["SUPABASE_URL"],
                       os.environ["SUPABASE_SERVICE_KEY"])
    sb.table("positions").select("ticker").limit(1).execute()
    check("Supabase connected and tables exist", True)
except Exception as e:
    msg = str(e)
    if "does not exist" in msg or "PGRST205" in msg:
        check("Supabase tables exist", False,
              "Connected, but tables are missing. Run schema.sql in the "
              "Supabase SQL Editor (README Step 3).")
    else:
        check("Supabase connected", False, msg[:150])

# --- 4. Does the Anthropic key work? --------------------------------------
try:
    from anthropic import Anthropic

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    client.messages.create(
        model=os.environ.get("MODEL", "claude-sonnet-5"),
        max_tokens=5,
        messages=[{"role": "user", "content": "hi"}],
    )
    check("Anthropic API key works", True)
except Exception as e:
    msg = str(e)
    if "credit" in msg.lower() or "billing" in msg.lower():
        check("Anthropic API key works", False,
              "Key is valid but the account has no credit. Add $5 at "
              "console.anthropic.com > Billing.")
    else:
        check("Anthropic API key works", False, msg[:150])

# --- 5. Does market data work? --------------------------------------------
try:
    import httpx

    r = httpx.get(
        "https://eodhd.com/api/real-time/AAPL.US",
        params={"api_token": os.environ["EODHD_API_KEY"], "fmt": "json"},
        timeout=15,
    )
    data = r.json()
    if isinstance(data, dict) and "close" in data:
        check(f"Market data works (AAPL = {data['close']})", True)

        # Confirm the non-US exchanges you actually hold are accessible.
        # The EOD-only plan and the free tier both fail here.
        probe = httpx.get(
            "https://eodhd.com/api/real-time/0700.HK",
            params={"api_token": os.environ["EODHD_API_KEY"], "fmt": "json"},
            timeout=15,
        )
        if probe.status_code == 200 and "close" in probe.json():
            check("Non-US exchanges accessible (HKEX)", True)
        else:
            check("Non-US exchanges accessible (HKEX)", False,
                  "US works but HKEX doesn't. You likely need the "
                  "'EOD+Intraday All World Extended' plan.")
    else:
        check("Market data works", False, str(data)[:150])
except Exception as e:
    check("Market data works", False, str(e)[:150])

# --- Summary ---------------------------------------------------------------
print()
if all(results):
    print("Everything works. Next:\n")
    print("  1. Edit scripts/sample_positions.csv with your real holdings")
    print("  2. python -m scripts.import_positions <your_telegram_id> "
          "scripts/sample_positions.csv")
    print("  3. python -m app.bot")
    print("\n  (Message @userinfobot on Telegram to get your telegram_id.)\n")
else:
    print(f"{sum(results)}/{len(results)} checks passed. "
          "Fix the [FAIL] items above and run this again.\n")
    sys.exit(1)
