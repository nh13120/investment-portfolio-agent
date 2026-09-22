"""
Message routing.

The single most important thing in this file is the DM guard. You have
per-user portfolios inside a shared group. If the bot answers a portfolio
question in the group, one person's positions and P&L land in everyone's
scrollback permanently. That check happens BEFORE the portfolio is even
resolved, in code -- not by asking the model to be discreet.
"""

from app import agent, charts, db, portfolio, telegram
from app.config import ALLOWED_USERS, SHARED_PORTFOLIO_OWNER

HELP_TEXT = """Portfolio bot. Message me directly with questions like:

- what do I hold?
- how is NVDA doing?
- what are my 5 biggest positions?
- what's my return this month?
- any news on LVMH?
- alert me if NVDA drops below 140

Commands:
/start  - register
/alerts - list active alerts
/reset  - clear our conversation history
/help   - this message

Works in DM, and in the group when shared-portfolio mode is on."""

GROUP_REFUSAL = (
    "Portfolio data stays in DMs -- message me directly and I'll answer there. "
    "Everyone in this group has their own portfolio, so I don't post positions "
    "or performance here."
)


async def handle_update(update: dict) -> None:
    """Process one Telegram update. Safe to call concurrently."""
    message = update.get("message")
    if not message or "text" not in message:
        return  # ignore photos, stickers, joins, edits, etc.

    chat_id = message["chat"]["id"]
    is_direct_message = message["chat"]["type"] == "private"
    telegram_user_id = message["from"]["id"]
    display_name = message["from"].get("first_name", "")
    text = message["text"].strip()

    # ---- Allowlist -------------------------------------------------------
    # Checked FIRST, before any database write, any API call, and any token
    # spend. An unknown user costs exactly one cheap Telegram reply.
    #
    # Their ID is logged so you can allowlist them if they're legitimate --
    # otherwise you'd have no way to find out who tried.
    if ALLOWED_USERS and telegram_user_id not in ALLOWED_USERS:
        print(f"[handlers] BLOCKED user {telegram_user_id} "
              f"({display_name!r}) -- not in ALLOWED_USERS")
        await telegram.send_message(
            chat_id,
            "This bot is private. If you should have access, ask the owner "
            f"to add your Telegram ID: {telegram_user_id}",
        )
        return

    # ---- Commands ---------------------------------------------------------
    if text.startswith("/"):
        command = text.split()[0].split("@")[0]  # strips /help@YourBotName

        if command in ("/start", "/help"):
            await telegram.send_message(chat_id, HELP_TEXT)
            if is_direct_message:
                db.get_or_create_user(telegram_user_id, display_name)
            return

        if command == "/reset":
            db.save_history(chat_id, [])
            await telegram.send_message(chat_id, "Conversation history cleared.")
            return

        if command == "/alerts" and (is_direct_message or SHARED_PORTFOLIO_OWNER):
            user = db.get_or_create_user(telegram_user_id, display_name)
            alerts = db.list_alerts(user["id"])
            if not alerts:
                await telegram.send_message(chat_id, "No active alerts.")
                return
            lines = [
                f"- {a['ticker']}: {a['kind']} @ {a['params'].get('threshold')}"
                for a in alerts
            ]
            await telegram.send_message(chat_id, "Active alerts:\n" + "\n".join(lines))
            return

    # ---- Privacy boundary -------------------------------------------------
    # In per-user mode this is essential: without it, one person's positions
    # and P&L land in everyone's group scrollback permanently.
    #
    # In SHARED mode there is exactly one book that everyone already has
    # access to, so there is nothing to leak and groups are the point.
    if not is_direct_message and not SHARED_PORTFOLIO_OWNER:
        await telegram.send_message(chat_id, GROUP_REFUSAL)
        return

    # ---- Normal question --------------------------------------------------
    await telegram.send_typing(chat_id)

    user = db.get_or_create_user(telegram_user_id, display_name)
    portfolio_id = db.get_portfolio_id(telegram_user_id, display_name)

    history = db.load_history(chat_id)

    try:
        reply, updated_history, chart_requests = await agent.answer(
            text, history, portfolio_id, user["id"]
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[handlers] agent failed: {exc}")
        await telegram.send_message(
            chat_id, "Something broke while answering that. Try again in a moment."
        )
        return

    db.save_history(chat_id, updated_history)
    await telegram.send_message(chat_id, reply)

    # Charts follow the text. Rendering happens here, not in the tool, so a
    # plotting failure degrades to "text arrived, image didn't" rather than
    # breaking the whole answer.
    for request in chart_requests[:2]:  # cap: two images per turn is plenty
        try:
            image = await _render_chart(request, portfolio_id)
        except Exception as exc:  # noqa: BLE001
            print(f"[handlers] chart render failed: {exc}")
            continue
        if image:
            await telegram.send_photo(chat_id, image)
        else:
            await telegram.send_message(
                chat_id, "(not enough data yet to draw that chart)")


async def _render_chart(request: dict, portfolio_id: str) -> bytes | None:
    """Turn a chart request from the model into PNG bytes."""
    kind = request.get("chart")

    if kind in ("composition", "movers"):
        rows = db.get_positions(portfolio_id)
        valued = await portfolio.value_positions(rows)
        positions = valued.get("positions", [])
        if kind == "composition":
            return charts.composition_chart(positions)
        return charts.movers_chart(positions)

    if kind == "price_history":
        ticker = request.get("ticker")
        if not ticker:
            return None
        held = db.get_positions(portfolio_id, ticker)
        ccy = held[0].get("ccy") if held else None
        avg_cost = held[0].get("avg_cost") if held else None
        since = portfolio.period_to_start_date(request.get("period", "3m"))
        from app import market
        bars = await market.get_history(ticker, ccy, from_date=since)
        return charts.price_history_chart(
            bars, ticker.upper(),
            float(avg_cost) if avg_cost else None,
        )

    if kind == "portfolio_value":
        since = portfolio.period_to_start_date(request.get("period", "3m"))
        snaps = db.get_snapshots(portfolio_id, since)
        return charts.portfolio_value_chart(snaps)

    return None