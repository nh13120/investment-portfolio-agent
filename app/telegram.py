"""
Thin wrapper over the Telegram Bot API.

Mental model: Telegram is not a socket. There are two separate HTTP flows.
  1. INBOUND  -- you ask Telegram for new messages (getUpdates), or Telegram
                 POSTs them to your server (webhook).
  2. OUTBOUND -- you POST to api.telegram.org to send a message.
Nothing links the two except the `chat_id` you carry across.
"""

import asyncio

import httpx

from app.config import TELEGRAM_TOKEN

BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# Telegram rejects any message over 4096 characters. With ~87 positions you
# WILL hit this, so we split before sending.
MAX_MESSAGE_LEN = 4000


async def call(method: str, **params) -> dict:
    """Call any Telegram Bot API method. See core.telegram.org/bots/api"""
    async with httpx.AsyncClient(timeout=35) as client:
        response = await client.post(f"{BASE_URL}/{method}", json=params)
        data = response.json()
        if not data.get("ok"):
            # Don't raise -- a failed send shouldn't crash the whole bot.
            print(f"[telegram] {method} failed: {data}")
        return data


def _split(text: str, limit: int = MAX_MESSAGE_LEN) -> list[str]:
    """Split long text into chunks, breaking on newlines where possible."""
    if len(text) <= limit:
        return [text]

    chunks, current = [], ""
    for line in text.split("\n"):
        # +1 accounts for the newline we'd be re-adding
        if len(current) + len(line) + 1 > limit:
            if current:
                chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


async def send_message(chat_id: int, text: str) -> None:
    """
    Send text to a chat, splitting if too long.

    Note: we deliberately do NOT set parse_mode. Telegram's Markdown parser
    rejects unescaped `_`, `*` and `[` -- and a ticker like BRK_B or any
    generated prose containing an underscore will make the ENTIRE send fail
    with a 400. Plain text always delivers. Only add parse_mode once you have
    a proper escaping function.
    """
    for chunk in _split(text):
        await call("sendMessage", chat_id=chat_id, text=chunk)


async def send_typing(chat_id: int) -> None:
    """Show the 'typing...' indicator so the bot doesn't look dead."""
    await call("sendChatAction", chat_id=chat_id, action="typing")


async def get_updates(offset: int | None, timeout: int = 30) -> list[dict]:
    """
    Long-poll for new messages. Used in local development.

    `offset` is how you acknowledge messages: passing offset=N tells Telegram
    "I've processed everything below N, don't send those again."
    """
    params = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    try:
        data = await call("getUpdates", **params)
        return data.get("result", [])
    except (httpx.ReadTimeout, httpx.ConnectError):
        # Normal during long polling -- just retry.
        await asyncio.sleep(1)
        return []


async def send_photo(chat_id: int, image_bytes: bytes,
                     caption: str = "") -> dict:
    """
    Send a PNG to a chat.

    Note this uses multipart/form-data, not JSON like every other method
    here -- Telegram requires the file as an upload. Hence the separate
    client call rather than reusing call().

    Captions are capped at 1024 characters by Telegram (shorter than the
    4096 for messages), so long analysis should be sent as a follow-up
    message rather than crammed into the caption.
    """
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{BASE_URL}/sendPhoto",
            data={"chat_id": chat_id, "caption": caption[:1024]},
            files={"photo": ("chart.png", image_bytes, "image/png")},
        )
        data = response.json()
        if not data.get("ok"):
            print(f"[telegram] sendPhoto failed: {str(data)[:200]}")
        return data