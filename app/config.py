"""
Central configuration. Every secret comes from environment variables.

Why this file exists: if a key is missing, you want to find out immediately at
startup with a clear message -- not three layers deep inside an API call with a
confusing 401 error.
"""

import os

from dotenv import load_dotenv

# Reads the .env file in the project root and puts those values into os.environ.
# On a deployed server there is no .env file -- the host injects the vars
# directly -- and load_dotenv() simply does nothing. Same code works in both.
load_dotenv()


def _required(name: str) -> str:
    """Fetch an env var, or crash with a message that says what to fix."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Missing environment variable: {name}\n"
            f"Add it to your .env file (see .env.example)."
        )
    return value


TELEGRAM_TOKEN = _required("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = _required("ANTHROPIC_API_KEY")
SUPABASE_URL = _required("SUPABASE_URL")
SUPABASE_SERVICE_KEY = _required("SUPABASE_SERVICE_KEY")
EODHD_API_KEY = _required("EODHD_API_KEY")

# Which Claude model handles chat. Sonnet is the right default here: strong at
# tool use, meaningfully cheaper than Opus. Swap to "claude-opus-5" if you find
# it fumbling multi-step questions.
MODEL = os.environ.get("MODEL", "claude-sonnet-5")

# Base currency for all reporting. Your positions can be in HKD/EUR/USD; every
# number the bot reports gets converted to this.
BASE_CCY = os.environ.get("BASE_CCY", "USD")

# How many past messages to keep per chat. Conversation history costs tokens on
# every single request, so this is a real cost lever.
MAX_HISTORY_MESSAGES = int(os.environ.get("MAX_HISTORY_MESSAGES", "20"))


def _parse_allowed_users(raw: str) -> set[int]:
    """
    Parse a comma-separated list of Telegram user IDs.

    Tolerates spaces, newlines and trailing commas, because this gets pasted
    into a hosting dashboard by hand. Non-numeric entries are skipped with a
    warning rather than crashing the bot at startup.
    """
    users = set()
    for chunk in raw.replace("\n", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            users.add(int(chunk))
        except ValueError:
            print(f"[config] WARNING: ignoring non-numeric ALLOWED_USERS "
                  f"entry: {chunk!r}")
    return users


# Telegram user IDs permitted to use this bot.
#
# WHY THIS MATTERS: bot usernames are publicly searchable. Anyone who finds
# @yourbot can message it, get a portfolio created, and run queries that bill
# YOUR Anthropic and EODHD accounts. An allowlist is the only thing standing
# between a searchable username and a stranger spending your API credit.
#
# Leave unset and the bot is open to anyone (a warning prints at startup).
ALLOWED_USERS = _parse_allowed_users(os.environ.get("ALLOWED_USERS", ""))


# --------------------------------------------------------------------------
# Shared portfolio mode
#
# When set, EVERY allowlisted user queries the same book -- an investment club
# rather than individual accounts. Because nothing is private in this mode,
# group chats are permitted: the DM-only guard exists solely to stop one
# person's positions leaking to another, and here there is only one position
# set that everyone already shares.
#
# Set it to the Telegram ID of whoever owns the portfolio (yours). Everyone
# else is transparently mapped onto that same portfolio_id.
#
# Leave blank for per-user portfolios (the default, DM-only).
# --------------------------------------------------------------------------
_shared_raw = os.environ.get("SHARED_PORTFOLIO_OWNER", "").strip()
SHARED_PORTFOLIO_OWNER: int | None = int(_shared_raw) if _shared_raw.isdigit() else None

# Optional: chat ID where alerts are posted. In shared mode this is usually
# the group, so everyone sees a trigger at the same time. Leave blank and
# alerts go to the DM of whoever created the rule.
_alert_raw = os.environ.get("ALERT_CHAT_ID", "").strip()
ALERT_CHAT_ID: int | None = (
    int(_alert_raw) if _alert_raw.lstrip("-").isdigit() else None
)