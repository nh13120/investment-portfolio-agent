"""
Portfolio mathematics. Everything numeric happens here, in Python.

This is the most important file in the project. The model is explicitly
forbidden from doing arithmetic -- it calls these functions and narrates the
JSON they return. Cost basis, weights, FX conversion and time-weighted returns
are exactly the kind of thing an LLM will produce a confident, plausible, wrong
number for. Here they are testable.
"""

from datetime import date, timedelta

from app.config import BASE_CCY
from app.market import get_fx_rate, get_quotes_by_ticker


async def value_positions(positions: list[dict]) -> dict:
    """
    Take raw holdings, attach live prices, convert to base currency, and
    compute unrealized P&L and portfolio weights.

    Returns a dict ready to hand to the model as a tool result.
    """
    if not positions:
        return {"positions": [], "total_value": 0.0, "base_ccy": BASE_CCY,
                "note": "No positions found. Import a CSV with /import first."}

    tickers = [p["ticker"] for p in positions]
    # EODHD needs an exchange suffix, which is inferred from each holding's
    # currency (HKD -> .HK, USD -> .US). Pass the mapping so market.py can
    # resolve symbols; plain tickers come back out the other side.
    ccy_by_ticker = {
        p["ticker"].upper(): (p.get("ccy") or BASE_CCY) for p in positions
    }
    # Full quotes (not just prices) so we keep previousClose and change_p.
    # EODHD returns these on every quote, so daily-move reporting is free.
    quotes = await get_quotes_by_ticker(tickers, ccy_by_ticker)

    # Cache FX rates so we fetch each currency pair at most once.
    fx_rates: dict[str, float] = {}
    for position in positions:
        ccy = (position.get("ccy") or BASE_CCY).upper()
        if ccy not in fx_rates:
            fx_rates[ccy] = await get_fx_rate(ccy, BASE_CCY)

    rows, total_value, total_cost = [], 0.0, 0.0
    total_day_change = 0.0

    for position in positions:
        ticker = position["ticker"]
        quote = quotes.get(ticker.upper())
        try:
            price = float(quote["close"]) if quote else None
        except (TypeError, ValueError, KeyError):
            price = None
        if price is None:
            # Be explicit about gaps rather than silently dropping the holding,
            # otherwise the totals quietly understate the portfolio.
            rows.append({"ticker": ticker, "qty": position["qty"],
                         "error": "no price available"})
            continue

        qty = float(position["qty"])
        ccy = (position.get("ccy") or BASE_CCY).upper()
        fx = fx_rates[ccy]

        market_value = qty * price * fx
        total_value += market_value

        row = {
            "ticker": ticker,
            "qty": qty,
            "price": round(price, 4),
            "ccy": ccy,
            "market_value_base": round(market_value, 2),
        }

        # Today's move, from previousClose. This is a DIFFERENT question from
        # unrealized P&L: "how did it move today" vs "am I up since I bought".
        try:
            prev_close = float(quote.get("previousClose"))
        except (TypeError, ValueError):
            prev_close = None
        if prev_close:
            day_change_base = (price - prev_close) * qty * fx
            row["prev_close"] = round(prev_close, 4)
            row["day_change_pct"] = round((price / prev_close - 1) * 100, 2)
            row["day_change_base"] = round(day_change_base, 2)
            total_day_change += day_change_base

        # avg_cost is optional -- unrealized P&L only exists if we have it.
        avg_cost = position.get("avg_cost")
        if avg_cost:
            cost_basis = qty * float(avg_cost) * fx
            total_cost += cost_basis
            row["avg_cost"] = round(float(avg_cost), 4)
            row["cost_basis_base"] = round(cost_basis, 2)
            row["unrealized_pnl_base"] = round(market_value - cost_basis, 2)
            row["unrealized_pnl_pct"] = round(
                (market_value / cost_basis - 1) * 100, 2
            ) if cost_basis else None

        rows.append(row)

    # Weights need the total, so this is a second pass.
    for row in rows:
        if "market_value_base" in row and total_value:
            row["weight_pct"] = round(row["market_value_base"] / total_value * 100, 2)

    result = {
        "base_ccy": BASE_CCY,
        "total_value_base": round(total_value, 2),
        "position_count": len(rows),
        "positions": sorted(
            rows, key=lambda r: r.get("market_value_base", 0), reverse=True
        ),
    }

    if total_day_change:
        result["total_day_change_base"] = round(total_day_change, 2)
        prior_value = total_value - total_day_change
        if prior_value:
            result["total_day_change_pct"] = round(
                total_day_change / prior_value * 100, 2
            )

    if total_cost:
        result["total_cost_basis_base"] = round(total_cost, 2)
        result["total_unrealized_pnl_base"] = round(total_value - total_cost, 2)
        result["total_unrealized_pnl_pct"] = round(
            (total_value / total_cost - 1) * 100, 2
        )
    else:
        result["note"] = (
            "No cost basis recorded, so profit/loss cannot be computed. "
            "Only current market values are available."
        )

    return result


def time_weighted_return(snapshots: list[dict]) -> dict:
    """
    Compute time-weighted return (TWR) from daily valuation snapshots.

    THE FORMULA AND WHY IT LOOKS LIKE THIS:

        daily return  r_t = (value_t - flow_t) / value_(t-1) - 1
        TWR = (1+r_1) * (1+r_2) * ... - 1

    Subtracting the day's external cash flow before dividing is what removes
    the distortion from deposits. If you hold $100k and deposit $10k, naive
    maths says you gained 10%. You gained nothing. TWR correctly reports 0%.

    This is the industry-standard measure precisely because it isolates
    investment performance from the timing and size of your contributions.
    """
    if len(snapshots) < 2:
        return {
            "error": "insufficient_history",
            "message": (
                "Need at least 2 daily snapshots to compute a return. "
                "The snapshot job builds this history over time -- "
                "check back in a few days."
            ),
            "snapshots_available": len(snapshots),
        }

    ordered = sorted(snapshots, key=lambda s: s["date"])
    cumulative = 1.0
    daily_returns = []

    for previous, current in zip(ordered, ordered[1:]):
        start_value = float(previous["total_value_base"])
        if start_value <= 0:
            continue

        end_value = float(current["total_value_base"])
        flow = float(current.get("net_flow_base") or 0.0)

        daily = (end_value - flow) / start_value - 1
        daily_returns.append(daily)
        cumulative *= 1 + daily

    twr = cumulative - 1
    days = len(daily_returns)

    output = {
        "method": "time_weighted_return",
        "period_start": ordered[0]["date"],
        "period_end": ordered[-1]["date"],
        "trading_days": days,
        "return_pct": round(twr * 100, 2),
        "start_value_base": round(float(ordered[0]["total_value_base"]), 2),
        "end_value_base": round(float(ordered[-1]["total_value_base"]), 2),
        "net_flows_base": round(
            sum(float(s.get("net_flow_base") or 0) for s in ordered[1:]), 2
        ),
    }

    # Annualise only when there's enough history for it to mean anything.
    # Annualising 5 days of data produces absurd numbers.
    if days >= 60:
        output["annualized_pct"] = round(((1 + twr) ** (252 / days) - 1) * 100, 2)

    # Max drawdown: the worst peak-to-trough fall over the period.
    peak, max_dd = float(ordered[0]["total_value_base"]), 0.0
    for snap in ordered:
        value = float(snap["total_value_base"])
        peak = max(peak, value)
        if peak > 0:
            max_dd = min(max_dd, value / peak - 1)
    output["max_drawdown_pct"] = round(max_dd * 100, 2)

    return output


def period_to_start_date(period: str) -> str:
    """Translate a human period label into an ISO start date."""
    today = date.today()
    offsets = {
        "1w": 7, "1m": 30, "3m": 90, "6m": 180,
        "1y": 365, "all": 3650,
    }
    if period == "ytd":
        return date(today.year, 1, 1).isoformat()
    return (today - timedelta(days=offsets.get(period, 30))).isoformat()


# ---------------------------------------------------------------------------
# Per-position performance from historical bars
#
# Design rule: these functions turn hundreds of price bars into ~10 numbers.
# The model never sees the raw series. A 250-day history is ~15,000 tokens
# raw; summarised it's about 120. Same answer, 1% of the cost.
# ---------------------------------------------------------------------------

def summarise_history(bars: list[dict], ticker: str, period: str) -> dict:
    """
    Compute return, range, volatility and drawdown from daily bars.

    Uses adjusted_close throughout, which accounts for dividends and splits.
    Raw close would report a false -50% on any 2:1 split and would ignore
    dividend income entirely.
    """
    if len(bars) < 2:
        return {
            "ticker": ticker,
            "error": "insufficient_history",
            "message": f"Not enough price history for {ticker} over {period}.",
        }

    def adj(bar):
        # Fall back to close if adjusted_close is absent (some exchanges).
        return float(bar.get("adjusted_close") or bar["close"])

    ordered = sorted(bars, key=lambda b: b["date"])
    first, last = adj(ordered[0]), adj(ordered[-1])

    closes = [adj(b) for b in ordered]
    highs = [float(b["high"]) for b in ordered]
    lows = [float(b["low"]) for b in ordered]

    # Daily returns -> annualised volatility (252 trading days).
    daily = [
        closes[i] / closes[i - 1] - 1
        for i in range(1, len(closes))
        if closes[i - 1]
    ]
    vol = None
    if len(daily) > 5:
        mean = sum(daily) / len(daily)
        variance = sum((r - mean) ** 2 for r in daily) / (len(daily) - 1)
        vol = (variance ** 0.5) * (252 ** 0.5) * 100

    # Worst peak-to-trough decline over the window.
    peak, max_dd = closes[0], 0.0
    for c in closes:
        peak = max(peak, c)
        if peak:
            max_dd = min(max_dd, c / peak - 1)

    result = {
        "ticker": ticker,
        "period": period,
        "start_date": ordered[0]["date"],
        "end_date": ordered[-1]["date"],
        "trading_days": len(ordered),
        "start_price": round(first, 4),
        "end_price": round(last, 4),
        "return_pct": round((last / first - 1) * 100, 2) if first else None,
        "period_high": round(max(highs), 4),
        "period_low": round(min(lows), 4),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "note": "Returns use adjusted close (dividends and splits included).",
    }
    if vol is not None:
        result["annualised_volatility_pct"] = round(vol, 1)

    # Where the current price sits in the period's range -- 0 = at the low,
    # 100 = at the high. A quick read on whether something is extended.
    span = max(highs) - min(lows)
    if span:
        result["pct_of_range"] = round((last - min(lows)) / span * 100, 1)

    return result


def summarise_intraday(bars: list[dict], ticker: str, interval: str) -> dict:
    """
    Condense intraday bars into a session summary plus a handful of
    evenly-spaced sample points, so the shape of the move is visible
    without shipping every bar.
    """
    if len(bars) < 2:
        return {
            "ticker": ticker,
            "error": "no_intraday_data",
            "message": (
                f"No intraday data for {ticker}. The market may be closed, "
                f"or this exchange may not have intraday coverage."
            ),
        }

    ordered = sorted(bars, key=lambda b: b["timestamp"])
    valid = [b for b in ordered if b.get("close") is not None]
    if len(valid) < 2:
        return {"ticker": ticker, "error": "no_intraday_data"}

    closes = [float(b["close"]) for b in valid]
    session_open, current = closes[0], closes[-1]

    # ~6 evenly spaced points: enough to see the trajectory, cheap in tokens.
    step = max(1, len(valid) // 6)
    samples = [
        {"time": valid[i]["datetime"], "price": round(float(valid[i]["close"]), 4)}
        for i in range(0, len(valid), step)
    ][:6]
    last_bar = valid[-1]
    if samples and samples[-1]["time"] != last_bar["datetime"]:
        samples.append({"time": last_bar["datetime"],
                        "price": round(current, 4)})

    return {
        "ticker": ticker,
        "interval": interval,
        "bars": len(valid),
        "session_start": valid[0]["datetime"],
        "session_end": last_bar["datetime"],
        "open": round(session_open, 4),
        "current": round(current, 4),
        "high": round(max(float(b["high"]) for b in valid), 4),
        "low": round(min(float(b["low"]) for b in valid), 4),
        "change_pct": round((current / session_open - 1) * 100, 2)
        if session_open else None,
        "path": samples,
        "note": "Times are UTC. Path shows a sample of the move, not every bar.",
    }