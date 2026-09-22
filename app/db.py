"""
Database layer. All Supabase reads/writes live here.

Everything runs through the SERVICE key, which bypasses Row Level Security.
That is why user isolation is enforced in code: every query is scoped by
portfolio_id, and portfolio_id is resolved from the Telegram user ID. Never
let a portfolio_id come from the model or from user text.
"""

from datetime import date, datetime, timezone

from supabase import Client, create_client

from app.config import (BASE_CCY, SHARED_PORTFOLIO_OWNER,
                        SUPABASE_SERVICE_KEY, SUPABASE_URL)

_client: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


# --------------------------------------------------------------------------
# Users & portfolios
# --------------------------------------------------------------------------

def get_or_create_user(telegram_user_id: int, display_name: str) -> dict:
    """Find the user by their Telegram ID, creating them on first contact."""
    found = (
        _client.table("users")
        .select("*")
        .eq("telegram_user_id", telegram_user_id)
        .execute()
    )
    if found.data:
        return found.data[0]

    created = (
        _client.table("users")
        .insert({"telegram_user_id": telegram_user_id, "display_name": display_name})
        .execute()
    )
    user = created.data[0]

    # Every new user gets one default portfolio so they can import right away.
    _client.table("portfolios").insert(
        {"user_id": user["id"], "name": "Main", "base_ccy": BASE_CCY}
    ).execute()

    return user


def get_portfolio_id(telegram_user_id: int, display_name: str = "") -> str:
    """
    Resolve a Telegram user to the portfolio UUID they should query.

    This is the security boundary of the whole app. Called once per message,
    before any tool runs. The model never sees or supplies this value.

    In SHARED mode every user is mapped onto the owner's portfolio, so the
    whole group reads and reasons over one book. The user record is still
    created individually -- alerts stay attributable to whoever set them.
    """
    # Register the caller regardless, so alerts and logs name a real person.
    get_or_create_user(telegram_user_id, display_name)

    if SHARED_PORTFOLIO_OWNER:
        user = get_or_create_user(SHARED_PORTFOLIO_OWNER, "portfolio owner")
    else:
        user = get_or_create_user(telegram_user_id, display_name)
    result = (
        _client.table("portfolios")
        .select("id")
        .eq("user_id", user["id"])
        .limit(1)
        .execute()
    )
    return result.data[0]["id"]


# --------------------------------------------------------------------------
# Positions
# --------------------------------------------------------------------------

def get_positions(portfolio_id: str, ticker: str | None = None) -> list[dict]:
    query = _client.table("positions").select("*").eq("portfolio_id", portfolio_id)
    if ticker:
        query = query.eq("ticker", ticker.upper())
    return query.execute().data


def upsert_position(portfolio_id: str, ticker: str, qty: float,
                    avg_cost: float | None, ccy: str) -> None:
    """Insert or update a holding. Used by the CSV importer."""
    _client.table("positions").upsert(
        {
            "portfolio_id": portfolio_id,
            "ticker": ticker.upper(),
            "qty": qty,
            "avg_cost": avg_cost,
            "ccy": ccy.upper(),
        },
        on_conflict="portfolio_id,ticker",
    ).execute()


# --------------------------------------------------------------------------
# Valuation snapshots -- the engine behind real return figures
# --------------------------------------------------------------------------

def save_snapshot(portfolio_id: str, total_value: float, net_flow: float = 0.0) -> None:
    """
    Record today's total portfolio value plus any external cash flow.

    `net_flow` is money you added or withdrew today (positive = deposit). It is
    what lets us separate "the portfolio grew" from "I put more money in".
    Without it every return figure is wrong.
    """
    _client.table("valuation_snaps").upsert(
        {
            "portfolio_id": portfolio_id,
            "date": date.today().isoformat(),
            "total_value_base": total_value,
            "net_flow_base": net_flow,
        },
        on_conflict="portfolio_id,date",
    ).execute()


def get_snapshots(portfolio_id: str, since: str | None = None) -> list[dict]:
    query = (
        _client.table("valuation_snaps")
        .select("*")
        .eq("portfolio_id", portfolio_id)
        .order("date")
    )
    if since:
        query = query.gte("date", since)
    return query.execute().data


def all_portfolio_ids() -> list[str]:
    """Every portfolio in the system. Used by the nightly snapshot job."""
    return [row["id"] for row in _client.table("portfolios").select("id").execute().data]


# --------------------------------------------------------------------------
# Alerts
# --------------------------------------------------------------------------

def create_alert(user_id: str, ticker: str, kind: str, params: dict) -> dict:
    return (
        _client.table("alert_rules")
        .insert(
            {
                "user_id": user_id,
                "ticker": ticker.upper(),
                "kind": kind,
                "params": params,
                "active": True,
            }
        )
        .execute()
        .data[0]
    )


def list_alerts(user_id: str) -> list[dict]:
    return (
        _client.table("alert_rules")
        .select("*")
        .eq("user_id", user_id)
        .eq("active", True)
        .execute()
        .data
    )


def deactivate_alert(alert_id: str) -> None:
    _client.table("alert_rules").update({"active": False}).eq("id", alert_id).execute()


def active_alerts() -> list[dict]:
    """All active alerts across all users, joined to the owner's Telegram ID."""
    return (
        _client.table("alert_rules")
        .select("*, users(telegram_user_id)")
        .eq("active", True)
        .execute()
        .data
    )


def mark_alert_fired(alert_id: str) -> None:
    _client.table("alert_rules").update(
        {"last_fired_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", alert_id).execute()


# --------------------------------------------------------------------------
# Conversation history & de-duplication
# --------------------------------------------------------------------------

def load_history(chat_id: int) -> list[dict]:
    result = (
        _client.table("chat_history")
        .select("messages")
        .eq("chat_id", chat_id)
        .execute()
    )
    return result.data[0]["messages"] if result.data else []


def save_history(chat_id: int, messages: list[dict]) -> None:
    _client.table("chat_history").upsert(
        {"chat_id": chat_id, "messages": messages}, on_conflict="chat_id"
    ).execute()


def already_processed(update_id: int) -> bool:
    """
    Telegram redelivers updates on any network hiccup. Without this check a
    single question can get answered twice. Returns True if we've seen it.
    """
    existing = (
        _client.table("processed_updates")
        .select("update_id")
        .eq("update_id", update_id)
        .execute()
    )
    if existing.data:
        return True
    _client.table("processed_updates").insert({"update_id": update_id}).execute()
    return False