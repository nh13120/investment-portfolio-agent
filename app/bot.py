"""
Local development entrypoint: long polling.

Run with:  python -m app.bot

Polling means YOUR process asks Telegram "anything new?" in a loop. No public
URL, no HTTPS certificate, no deployment needed -- it works from your laptop
immediately. That is why you start here rather than with webhooks.

The tradeoff: it only runs while this process runs. Close the terminal and the
bot goes silent. Deployment (see README Step 8) is what fixes that.
"""

import asyncio

from app import db, handlers, telegram
from app.config import ALLOWED_USERS, SHARED_PORTFOLIO_OWNER


async def main() -> None:
    if ALLOWED_USERS:
        print(f"Allowlist active: {len(ALLOWED_USERS)} permitted user(s).")
    else:
        print("=" * 62)
        print("WARNING: ALLOWED_USERS is not set.")
        print("Anyone who finds this bot's username can use it, and their")
        print("queries will bill YOUR Anthropic and EODHD accounts.")
        print("Set ALLOWED_USERS to a comma-separated list of Telegram IDs.")
        print("=" * 62)

    if SHARED_PORTFOLIO_OWNER:
        print(f"SHARED portfolio mode: all users query the book owned by "
              f"{SHARED_PORTFOLIO_OWNER}. Group chats enabled.")
    else:
        print("Per-user portfolios. Portfolio questions are DM-only.")

    print("Bot started. Polling for messages... (Ctrl+C to stop)")

    # If a webhook was set previously, getUpdates will refuse to work until
    # it's removed. Telegram allows exactly one delivery method at a time.
    await telegram.call("deleteWebhook")

    offset: int | None = None

    while True:
        updates = await telegram.get_updates(offset)

        for update in updates:
            # Advance the offset immediately so a crash on one message doesn't
            # cause Telegram to redeliver it forever in a loop.
            offset = update["update_id"] + 1

            if db.already_processed(update["update_id"]):
                continue

            # Handle in the background so a slow answer doesn't block the
            # polling loop and stall everyone else's messages.
            asyncio.create_task(handlers.handle_update(update))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")