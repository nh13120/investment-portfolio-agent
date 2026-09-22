"""
Find your Telegram user ID using your OWN bot -- no third-party bot required.

HOW TO USE:
  1. Make sure TELEGRAM_TOKEN is filled in inside your .env file.
  2. Open Telegram, find your bot, and send it any message (e.g. "hello").
  3. Run:  python scripts/whoami.py

Why this works: every message Telegram delivers to your bot includes the
sender's numeric ID. This script just asks Telegram for recent messages and
prints who sent them.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from dotenv import load_dotenv

load_dotenv()

token = os.environ.get("TELEGRAM_TOKEN", "")
if not token or "xxxx" in token:
    print("\nTELEGRAM_TOKEN is not set in your .env file yet.")
    print("Get it from @BotFather first, paste it into .env, then run this again.\n")
    sys.exit(1)

base = f"https://api.telegram.org/bot{token}"

# Confirm the token is valid and tell the user which bot to message.
try:
    me = httpx.get(f"{base}/getMe", timeout=15).json()
except Exception as e:
    print(f"\nCouldn't reach Telegram: {e}\n")
    sys.exit(1)

if not me.get("ok"):
    print("\nYour TELEGRAM_TOKEN was rejected by Telegram.")
    print("Re-copy it from @BotFather -- it should look like 7123456789:AAH8x...\n")
    sys.exit(1)

bot_username = me["result"]["username"]
print(f"\nBot connected: @{bot_username}")

# A webhook, if set, swallows updates before getUpdates can see them.
httpx.post(f"{base}/deleteWebhook", timeout=15)

updates = httpx.get(f"{base}/getUpdates", timeout=20).json().get("result", [])

# Collect every distinct human who has messaged the bot.
people = {}
for update in updates:
    message = update.get("message") or update.get("edited_message")
    if not message:
        continue
    sender = message.get("from", {})
    if sender.get("is_bot"):
        continue
    people[sender["id"]] = " ".join(
        filter(None, [sender.get("first_name"), sender.get("last_name")])
    ) or sender.get("username", "unknown")

if not people:
    print(f"""
No messages found yet.

DO THIS:
  1. Open Telegram
  2. Search for:  @{bot_username}
  3. Open the chat and tap START (or just send "hello")
  4. Run this script again

(If you sent a message more than 24 hours ago, Telegram has already
 discarded it -- just send a fresh one.)
""")
    sys.exit(1)

print("\nFound these Telegram IDs:\n")
for user_id, name in people.items():
    print(f"  {user_id}   ({name})")

first_id = list(people)[0]
print(f"""
Your ID is the number above. Use it in the import command:

  python -m scripts.import_positions {first_id} scripts/sample_positions.csv
""")
