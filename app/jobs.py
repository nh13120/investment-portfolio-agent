"""
Scheduled jobs. These run on a timer, independent of the chat process.

Two jobs:
  snapshot  -- once daily after market close. Records portfolio value so that
               return figures become possible. Run this FIRST, from day one:
               it is the only thing here that gets more valuable with age, and
               every day you delay is a day missing from your track record.
  alerts    -- every ~15 minutes during market hours. Checks price rules.

Run manually:
    python -m app.jobs snapshot
    python -m app.jobs alerts
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone

from app import db, market, portfolio, telegram
from app.config import ALERT_CHAT_ID

# Don't re-fire the same alert more often than this. Without it, a stock
# sitting just below your threshold will notify you every 15 minutes all day
# and you will mute the bot.
ALERT_COOLDOWN_HOURS = 12


async def run_snapshot() -> None:
    """Record today's total value for every portfolio."""
    for portfolio_id in db.all_portfolio_ids():
        positions = db.get_positions(portfolio_id)
        if not positions:
            continue

        valued = await portfolio.value_positions(positions)
        total = valued["total_value_base"]

        # net_flow stays 0 here. If you deposit or withdraw, update that day's
        # row manually or the return figure will misattribute the cash move to
        # investment performance.
        db.save_snapshot(portfolio_id, total, net_flow=0.0)
        print(f"[snapshot] {portfolio_id}: {total:,.2f}")


def _should_fire(alert: dict, price: float, prev_close: float | None) -> str | None:
    """Return an alert message if the rule is triggered, else None."""
    kind = alert["kind"]
    threshold = float(alert["params"]["threshold"])
    ticker = alert["ticker"]

    if kind == "price_above" and price > threshold:
        return f"{ticker} is at {price:,.2f}, above your {threshold:,.2f} alert."

    if kind == "price_below" and price < threshold:
        return f"{ticker} is at {price:,.2f}, below your {threshold:,.2f} alert."

    if kind == "pct_move_day" and prev_close:
        move = (price / prev_close - 1) * 100
        if abs(move) >= threshold:
            direction = "up" if move > 0 else "down"
            return (
                f"{ticker} is {direction} {abs(move):.1f}% today "
                f"at {price:,.2f} (threshold {threshold}%)."
            )

    return None


async def run_alerts() -> None:
    """Evaluate every active alert and notify owners of any that trigger."""
    alerts = db.active_alerts()
    if not alerts:
        return

    # One batched call for all tickers, not one call per alert.
    # get_quotes (not get_prices) because we need previousClose for
    # percentage-move alerts -- EODHD includes it in every quote.
    symbols = {a["ticker"]: market.resolve_symbol(a["ticker"]) for a in alerts}
    quotes = await market.get_quotes(list(set(symbols.values())))

    now = datetime.now(timezone.utc)

    for alert in alerts:
        quote = quotes.get(symbols[alert["ticker"]])
        if not quote:
            continue
        try:
            price = float(quote["close"])
        except (TypeError, ValueError, KeyError):
            continue
        try:
            prev_close = float(quote.get("previousClose"))
        except (TypeError, ValueError):
            prev_close = None

        # Cooldown check.
        last_fired = alert.get("last_fired_at")
        if last_fired:
            fired_at = datetime.fromisoformat(last_fired.replace("Z", "+00:00"))
            if now - fired_at < timedelta(hours=ALERT_COOLDOWN_HOURS):
                continue

        message = _should_fire(alert, price, prev_close)
        if not message:
            continue

        # Where the alert lands. With ALERT_CHAT_ID set (shared mode), it
        # goes to the group so everyone sees a trigger simultaneously.
        # Otherwise it goes to the DM of whoever created the rule -- which is
        # the correct default when portfolios are per-user and private.
        destination = ALERT_CHAT_ID or alert["users"]["telegram_user_id"]
        await telegram.send_message(destination, f"Alert: {message}")
        db.mark_alert_fired(alert["id"])
        print(f"[alert] fired for {alert['ticker']}")


if __name__ == "__main__":
    job = sys.argv[1] if len(sys.argv) > 1 else "snapshot"
    asyncio.run(run_snapshot() if job == "snapshot" else run_alerts())