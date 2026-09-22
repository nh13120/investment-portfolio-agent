"""
The tool surface exposed to Claude.

Design rule: the model gets VERBS, not database access. There is no
"run_sql" tool and there never should be. Each tool is a narrow, audited
function. If Claude wants a number, it must ask for it through one of these,
which means every number the user sees was computed by code you can test.

Note what is NOT in any schema: portfolio_id. The model can't request another
person's data because it has no way to name one. The ID is injected by the
dispatcher from the verified Telegram user ID.
"""

from app import db, market, portfolio

TOOL_SCHEMAS = [
    {
        "name": "get_positions",
        "description": (
            "Current holdings with live prices, market values in base currency, "
            "portfolio weights, unrealized profit/loss where cost basis is "
            "known, AND today's price move (day_change_pct, day_change_base, "
            "prev_close per position; total_day_change_pct for the portfolio). "
            "Use for 'what do I hold', 'how is Nvidia doing', 'what are my "
            "biggest positions', 'am I up on X', and also for 'what moved "
            "today', 'biggest movers today', 'how is my portfolio doing "
            "today'. Note day_change_pct (today's move) is a different "
            "question from unrealized_pnl_pct (gain since purchase) -- pick "
            "the right one for what was asked."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": (
                        "Single ticker to look up, e.g. 'NVDA'. Omit to return "
                        "the entire portfolio."
                    ),
                }
            },
        },
    },
    {
        "name": "get_performance",
        "description": (
            "Time-weighted return over a period, computed from daily valuation "
            "snapshots. Correctly excludes the effect of deposits and "
            "withdrawals. Also returns max drawdown. Use for 'how am I doing', "
            "'what's my return this month', 'how bad was the drawdown'. "
            "Returns an error if not enough snapshot history exists yet -- "
            "report that honestly rather than estimating."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "enum": ["1w", "1m", "3m", "6m", "1y", "ytd", "all"],
                    "description": "Lookback window. Defaults to 1m.",
                }
            },
        },
    },
    {
        "name": "get_news",
        "description": (
            "Financial news with per-article sentiment scores, from EODHD. "
            "Filter by ticker, by topic tag, and by date range. Each article "
            "returns headline, excerpt, link, sentiment (polarity plus "
            "pos/neu/neg), topic tags, and other tickers mentioned -- the "
            "last is useful for spotting read-across to the rest of the "
            "portfolio. Use for 'why did X move', 'any news on Y', 'what's "
            "happening in semis'. Prefer this over web search for a specific "
            "holding; use web search for broader context, or when this "
            "returns nothing. Requests are expensive (5 API calls plus 5 per "
            "ticker), so don't call it speculatively."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": (
                        "Exchange suffix for non-US: '0700.HK', 'MC.PA'. "
                        "Omit to search by topic or get the general feed."
                    ),
                },
                "topic": {
                    "type": "string",
                    "description": (
                        "Topic tag instead of a ticker, e.g. 'earnings "
                        "report', 'merger', 'price target', 'artificial "
                        "intelligence'. Supply ticker OR topic."
                    ),
                },
                "from_date": {
                    "type": "string",
                    "description": "Start date YYYY-MM-DD, inclusive.",
                },
                "to_date": {
                    "type": "string",
                    "description": "End date YYYY-MM-DD, inclusive.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Articles to return, 1-50. Default 10.",
                },
            },
        },
    },
    {
        "name": "get_sentiment",
        "description": (
            "Daily aggregated news sentiment for one or more tickers, scored "
            "-1 (very negative) to +1 (very positive), with the article count "
            "behind each day's score. Use for 'has sentiment on X turned', "
            "'how does the market feel about my HK names', or to compare "
            "sentiment trend against price action. ALWAYS read the score "
            "alongside its count -- a score from 2 articles is noise, one "
            "from 60 is signal. Accepts several tickers in one call, which is "
            "much cheaper than calling it repeatedly."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tickers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Tickers with exchange suffix where non-US, e.g. "
                        "['NVDA', '0700.HK']."
                    ),
                },
                "from_date": {"type": "string", "description": "YYYY-MM-DD"},
                "to_date": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["tickers"],
        },
    },
    {
        "name": "create_alert",
        "description": (
            "Create a price or movement alert that fires automatically. "
            "Use when the user asks to be notified or warned about something. "
            "Always confirm back what was created, in plain language."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": (
                        "Use the exchange suffix for non-US listings: "
                        "'NVDA' or 'NVDA.US', but '0700.HK' for Tencent and "
                        "'MC.PA' for LVMH. Plain tickers default to US."
                    ),
                },
                "kind": {
                    "type": "string",
                    "enum": ["price_above", "price_below", "pct_move_day"],
                    "description": (
                        "price_above/price_below trigger at an absolute level. "
                        "pct_move_day triggers on a daily move larger than a "
                        "threshold in either direction."
                    ),
                },
                "threshold": {
                    "type": "number",
                    "description": (
                        "Price level for price_above/price_below; percentage "
                        "(e.g. 5 for 5%) for pct_move_day."
                    ),
                },
            },
            "required": ["ticker", "kind", "threshold"],
        },
    },
    {
        "name": "get_price_history",
        "description": (
            "Historical performance for ONE ticker over a period: return, "
            "high/low range, annualised volatility, max drawdown, and where "
            "the price sits in its range. Uses adjusted close, so dividends "
            "and splits are included. Use for 'how has Tencent done this "
            "year', 'what's NVDA's volatility', 'how far is X off its high'. "
            "This is about the STOCK's performance -- use get_performance for "
            "the whole portfolio, and get_positions for gain versus what the "
            "user actually paid."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": (
                        "Exchange suffix for non-US: '0700.HK', 'MC.PA'. "
                        "Plain tickers default to US."
                    ),
                },
                "period": {
                    "type": "string",
                    "enum": ["1w", "1m", "3m", "6m", "1y", "ytd", "all"],
                    "description": "Lookback window. Defaults to 3m.",
                },
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_intraday",
        "description": (
            "Today's intraday price action for ONE ticker: session open, "
            "current, high, low, percent change, and a sampled path showing "
            "the shape of the move. Use for 'what happened to Tencent "
            "today', 'when did NVDA drop', 'how has it traded this session'. "
            "For a simple current price or today's percent move across "
            "holdings, get_positions is cheaper -- use this only when the "
            "user wants the intraday PATH, not just the net change."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Exchange suffix for non-US, e.g. '0700.HK'.",
                },
                "interval": {
                    "type": "string",
                    "enum": ["1m", "5m", "1h"],
                    "description": "Bar size. Defaults to 5m.",
                },
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "send_chart",
        "description": (
            "Send a visual chart to the chat. Use when a picture genuinely "
            "beats numbers: 'show me my allocation', 'chart Tencent', "
            "'what moved today', 'graph my portfolio value'. Also offer one "
            "unprompted when discussing concentration or a long price "
            "history, where a list of numbers is hard to read on a phone. "
            "The chart is sent as an image immediately after your reply, so "
            "keep the accompanying text short and do NOT describe what the "
            "chart looks like -- the user can see it. Add only what the "
            "image cannot convey."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "chart": {
                    "type": "string",
                    "enum": ["composition", "price_history",
                             "portfolio_value", "movers"],
                    "description": (
                        "composition = holdings by weight; "
                        "price_history = one ticker over time with the "
                        "user's cost basis marked; "
                        "portfolio_value = total value over time (needs "
                        "daily snapshots); "
                        "movers = today's biggest moves up and down."
                    ),
                },
                "ticker": {
                    "type": "string",
                    "description": "Required for price_history only.",
                },
                "period": {
                    "type": "string",
                    "enum": ["1w", "1m", "3m", "6m", "1y", "ytd", "all"],
                    "description": "For price_history and portfolio_value. Default 3m.",
                },
            },
            "required": ["chart"],
        },
    },
    {
        "name": "list_alerts",
        "description": "List the user's currently active alerts.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


async def dispatch(name: str, args: dict, portfolio_id: str, user_id: str,
                   chart_requests: list | None = None) -> dict:
    """
    Execute a tool by name.

    `portfolio_id` and `user_id` come from the verified Telegram sender, NOT
    from `args`. This is what keeps one user's tools from touching another
    user's data even if the model were somehow persuaded to try.
    """
    chart_requests = chart_requests if chart_requests is not None else []

    if name == "get_positions":
        rows = db.get_positions(portfolio_id, args.get("ticker"))
        return await portfolio.value_positions(rows)

    if name == "get_performance":
        period = args.get("period", "1m")
        since = portfolio.period_to_start_date(period)
        snapshots = db.get_snapshots(portfolio_id, since)
        result = portfolio.time_weighted_return(snapshots)
        result["period_requested"] = period
        return result

    if name == "get_news":
        ticker = args.get("ticker")
        # Resolve the holding's currency so the exchange suffix is right.
        ccy = None
        if ticker:
            held = db.get_positions(portfolio_id, ticker)
            ccy = held[0].get("ccy") if held else None
        articles = await market.get_news(
            ticker=ticker,
            topic=args.get("topic"),
            from_date=args.get("from_date"),
            to_date=args.get("to_date"),
            limit=args.get("limit", 10),
            ccy=ccy,
        )
        if not articles:
            return {
                "ticker": ticker,
                "topic": args.get("topic"),
                "articles": [],
                "note": ("No articles returned. Either nothing was published "
                         "in this window, or this plan lacks news access. "
                         "Try web search instead."),
            }
        return {"ticker": ticker, "topic": args.get("topic"),
                "count": len(articles), "articles": articles}

    if name == "get_sentiment":
        tickers = args["tickers"]
        ccy_map = {}
        for t in tickers:
            held = db.get_positions(portfolio_id, t)
            if held:
                ccy_map[t.upper()] = held[0].get("ccy")
        data = await market.get_sentiment(
            tickers,
            from_date=args.get("from_date"),
            to_date=args.get("to_date"),
            ccy_by_ticker=ccy_map,
        )
        if not data:
            return {"sentiment": {}, "note": "No sentiment data returned."}
        return {
            "sentiment": data,
            "note": ("normalized runs -1 to +1. Read it together with count: "
                     "a score from few articles is noise."),
        }

    if name == "create_alert":
        alert = db.create_alert(
            user_id=user_id,
            ticker=args["ticker"],
            kind=args["kind"],
            params={"threshold": args["threshold"]},
        )
        return {"created": True, "alert_id": alert["id"],
                "ticker": alert["ticker"], "kind": alert["kind"],
                "threshold": args["threshold"]}

    if name == "get_price_history":
        ticker = args["ticker"]
        period = args.get("period", "3m")
        since = portfolio.period_to_start_date(period)
        # Look up the holding's currency so the exchange suffix resolves.
        held = db.get_positions(portfolio_id, ticker)
        ccy = held[0].get("ccy") if held else None
        bars = await market.get_history(ticker, ccy, from_date=since)
        return portfolio.summarise_history(bars, ticker.upper(), period)

    if name == "get_intraday":
        ticker = args["ticker"]
        interval = args.get("interval", "5m")
        held = db.get_positions(portfolio_id, ticker)
        ccy = held[0].get("ccy") if held else None
        bars = await market.get_intraday(ticker, ccy, interval)
        return portfolio.summarise_intraday(bars, ticker.upper(), interval)

    if name == "send_chart":
        # Charts cannot travel back through a tool_result, which must be text.
        # So this returns a REQUEST that handlers.py fulfils after the agent
        # finishes: render the PNG, then sendPhoto. The model gets a short
        # confirmation so it knows the chart is on its way and can write
        # around it rather than describing it.
        kind = args["chart"]
        request = {"chart": kind}
        if args.get("ticker"):
            request["ticker"] = args["ticker"]
        if args.get("period"):
            request["period"] = args["period"]
        chart_requests.append(request)
        return {
            "chart_queued": kind,
            "note": ("The chart will be sent as an image right after your "
                     "reply. Do not describe its appearance."),
        }

    if name == "list_alerts":
        alerts = db.list_alerts(user_id)
        return {
            "alerts": [
                {"ticker": a["ticker"], "kind": a["kind"],
                 "threshold": a["params"].get("threshold"),
                 "last_fired_at": a.get("last_fired_at")}
                for a in alerts
            ]
        }

    return {"error": f"Unknown tool: {name}"}