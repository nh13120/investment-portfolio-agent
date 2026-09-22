"""
Find the chat ID of a Telegram group, for ALERT_CHAT_ID.

    1. Add your bot to the group
    2. Send any message in that group (start it with / so the bot sees it,
       e.g. "/start" -- see the privacy-mode note below)
    3. Run:  python scripts/find_chat_id.py

Group chat IDs are NEGATIVE (e.g. -1001234567890). That's normal and the
minus sign is part of the ID -- include it.

PRIVACY MODE: by default a Telegram bot only receives group messages that
start with "/" or that @mention it. To let it read everything in the group,
message @BotFather -> /setprivacy -> pick your bot -> Disable. Only do that
if you want the bot seeing all group chatter.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from dotenv import load_dotenv

load_dotenv()

token = os.environ.get("TELEGRAM_TOKEN", "")
if not token:
    print("\nTELEGRAM_TOKEN not set in .env\n")
    sys.exit(1)

base = f"https://api.telegram.org/bot{token}"
httpx.post(f"{base}/deleteWebhook", timeout=15)
updates = httpx.get(f"{base}/getUpdates", timeout=20).json().get("result", [])

chats = {}
for u in updates:
    msg = u.get("message") or u.get("edited_message") or u.get("channel_post")
    if not msg:
        continue
    chat = msg.get("chat", {})
    chats[chat.get("id")] = (chat.get("type"), chat.get("title") or chat.get("first_name"))

if not chats:
    print("""
No chats found.

  1. Add the bot to your group
  2. Send "/start" in the group (the leading slash matters -- see privacy note)
  3. Run this again

Telegram discards undelivered updates after 24h, so send a fresh message.
""")
    sys.exit(1)

print("\nChats this bot can see:\n")
for cid, (ctype, title) in chats.items():
    marker = "  <-- use this for ALERT_CHAT_ID" if ctype in ("group", "supergroup") else ""
    print(f"  {cid:<18} {ctype:<12} {title}{marker}")
print()
