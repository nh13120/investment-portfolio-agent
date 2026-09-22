"""
Market data via EODHD.

Replaces the Twelve Data implementation. Function signatures are unchanged on
purpose -- portfolio.py and jobs.py call the same names with the same
arguments, so swapping providers touches only this file.

Two EODHD specifics shape the code below:

1. TICKER SUFFIXES. EODHD identifies instruments as SYMBOL.EXCHANGE:
       NVDA.US   MC.PA   0700.HK   ASML.AS
   Your CSV stays in plain form (NVDA, 0700) and `resolve_symbol` adds the
   suffix. See the EUR note below -- it's the one case needing care.

2. previousClose IS INCLUDED in every quote. Twelve Data didn't give this
   cheaply, which is why the pct_move_day alert was stubbed out. It now works.
"""

import asyncio
import time

import httpx

from app.config import EODHD_API_KEY

BASE_URL = "https://eodhd.com/api"

# In-process cache: {symbol: (quote_dict, fetched_at)}
_cache: dict[str, tuple[dict, float]] = {}
CACHE_TTL_SECONDS = 60

# Currency -> exchange suffix, used when a CSV ticker has no explicit suffix.
#
# WARNING ABOUT EUR: the eurozone spans many exchanges (Paris .PA,
# Amsterdam .AS, Frankfurt .XETRA, Milan .MI, Madrid .MC...). Currency alone
# cannot distinguish them, so EUR defaults to Paris. For any non-Paris
# European holding, WRITE THE FULL SYMBOL IN YOUR CSV -- e.g. ASML.AS.
# Anything containing a "." is passed through untouched.
SUFFIX_BY_CCY = {
    "USD": "US",
    "HKD": "HK",
    "GBP": "LSE",
    "GBX": "LSE",
    "JPY": "TSE",
    "CHF": "SW",
    "CAD": "TO",
    "AUD": "AU",
    "SGD": "SG",
    "CNY": "SHG",
    "EUR": "PA",   # default only -- override in the CSV where needed
}


def resolve_symbol(ticker: str, ccy: str | None = None) -> str:
    """
    Turn a CSV ticker into an EODHD symbol.

        resolve_symbol("NVDA", "USD")     -> "NVDA.US"
        resolve_symbol("0700", "HKD")     -> "0700.HK"
        resolve_symbol("ASML.AS", "EUR")  -> "ASML.AS"   (explicit wins)
        resolve_symbol("NVDA")            -> "NVDA.US"   (US fallback)
    """
    ticker = ticker.strip().upper()
    if "." in ticker:
        return ticker  # already explicit, trust it
    suffix = SUFFIX_BY_CCY.get((ccy or "USD").upper(), "US")
    return f"{ticker}.{suffix}"


def plain_ticker(symbol: str) -> str:
    """Strip the exchange suffix for display: 'NVDA.US' -> 'NVDA'."""
    return symbol.rsplit(".", 1)[0]


async def get_quotes(symbols: list[str]) -> dict[str, dict]:
    """
    Fetch full quotes for EODHD symbols. Returns {symbol: quote_dict}.

    Each quote contains close, previousClose, change, change_p, open, high,
    low, volume. Symbols that fail are omitted -- callers must handle missing
    keys rather than assuming everything came back.
    """
    symbols = [s.upper() for s in symbols]
    quotes: dict[str, dict] = {}

    missing = []
    for symbol in symbols:
        entry = _cache.get(symbol)
        if entry and (time.time() - entry[1]) < CACHE_TTL_SECONDS:
            quotes[symbol] = entry[0]
        else:
            missing.append(symbol)

    if not missing:
        return quotes

    # EODHD batches via: /real-time/FIRST?s=SECOND,THIRD,...
    # Chunked at 20 to limit the blast radius of a failure.
    for i in range(0, len(missing), 20):
        batch = missing[i : i + 20]
        found = await _fetch_batch(batch)

        # CRITICAL: EODHD returns a non-JSON error for the ENTIRE request if
        # one symbol is unresolvable -- so a single delisted ticker silently
        # wipes out the other 19. That produced a portfolio total that looked
        # plausible but excluded ~30 holdings.
        #
        # So: any symbol the batch didn't return gets retried on its own.
        # Costs a few extra calls, and your plan allows 1000/minute.
        unreturned = [s for s in batch if s not in found]
        if unreturned and len(unreturned) > 1:
            print(f"[market] batch incomplete ({len(unreturned)} missing), "
                  f"retrying individually")

        # Fire the retries CONCURRENTLY. Sequentially, 40 recoveries at
        # ~300ms each stalls the answer for 15+ seconds; in parallel it's
        # roughly one round trip. Your plan allows 1000 req/min, so there
        # is ample headroom for this.
        if unreturned:
            singles = await asyncio.gather(
                *(_fetch_batch([s]) for s in unreturned)
            )
            for single in singles:
                found.update(single)

        for code, quote in found.items():
            quotes[code] = quote
            _cache[code] = (quote, time.time())

        still_missing = [s for s in batch if s not in found]
        for symbol in still_missing:
            print(f"[market] no price available for {symbol}")

    return quotes


async def _fetch_batch(batch: list[str]) -> dict[str, dict]:
    """
    Fetch one group of symbols. Returns {symbol: quote} for whatever came
    back -- an empty dict if the request failed entirely. Never raises, so a
    network blip degrades one batch rather than the whole answer.
    """
    if not batch:
        return {}

    first, rest = batch[0], batch[1:]
    params = {"api_token": EODHD_API_KEY, "fmt": "json"}
    if rest:
        params["s"] = ",".join(rest)

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{BASE_URL}/real-time/{first}", params=params
            )
        if response.status_code != 200:
            return {}
        data = response.json()
    except Exception:  # noqa: BLE001
        # Includes JSONDecodeError, which is exactly what a bad symbol in the
        # URL path produces (EODHD replies with plain text, not JSON).
        return {}

    # One symbol returns a flat object; several return a list. Normalise.
    rows = data if isinstance(data, list) else [data]

    out = {}
    for quote in rows:
        if not isinstance(quote, dict):
            continue
        code = str(quote.get("code", "")).upper()
        if not code or quote.get("close") in (None, "NA"):
            continue
        out[code] = quote
    return out


async def get_quotes_by_ticker(
    tickers: list[str], ccy_by_ticker: dict[str, str] | None = None
) -> dict[str, dict]:
    """
    Full quotes keyed by the ORIGINAL ticker the caller passed in.

    Like get_prices, but keeps the whole quote so callers can use
    previousClose and change_p for daily-move reporting. EODHD includes
    these on every quote at no extra request cost.
    """
    ccy_by_ticker = ccy_by_ticker or {}

    symbol_to_ticker = {}
    for ticker in tickers:
        symbol = resolve_symbol(ticker, ccy_by_ticker.get(ticker.upper()))
        symbol_to_ticker[symbol] = ticker.upper()

    quotes = await get_quotes(list(symbol_to_ticker))

    return {
        symbol_to_ticker.get(symbol, plain_ticker(symbol)): quote
        for symbol, quote in quotes.items()
    }


async def get_prices(tickers: list[str],
                     ccy_by_ticker: dict[str, str] | None = None) -> dict[str, float]:
    """
    Latest prices keyed by the ORIGINAL ticker the caller passed in.

    Callers work in plain tickers ('NVDA'); the EODHD suffix is an
    implementation detail handled here and mapped back on the way out.
    """
    ccy_by_ticker = ccy_by_ticker or {}

    symbol_to_ticker = {}
    for ticker in tickers:
        symbol = resolve_symbol(ticker, ccy_by_ticker.get(ticker.upper()))
        symbol_to_ticker[symbol] = ticker.upper()

    quotes = await get_quotes(list(symbol_to_ticker))

    prices = {}
    for symbol, quote in quotes.items():
        ticker = symbol_to_ticker.get(symbol, plain_ticker(symbol))
        try:
            prices[ticker] = float(quote["close"])
        except (TypeError, ValueError, KeyError):
            continue
    return prices


async def get_fx_rate(from_ccy: str, to_ccy: str) -> float:
    """
    FX rate for converting position values into base currency.

    EODHD forex symbols are six-letter pairs with a .FOREX suffix, e.g.
    HKDUSD.FOREX. Falls back to the inverse pair, then to 1.0 with a loud
    warning -- silently treating HKD as USD would overstate the portfolio
    by roughly 8x, so this must never fail quietly.
    """
    from_ccy, to_ccy = from_ccy.upper(), to_ccy.upper()
    if from_ccy == to_ccy:
        return 1.0

    pair = f"{from_ccy}{to_ccy}.FOREX"
    quote = (await get_quotes([pair])).get(pair)
    if quote:
        try:
            rate = float(quote["close"])
            if rate:
                return rate
        except (TypeError, ValueError, KeyError):
            pass

    inverse = f"{to_ccy}{from_ccy}.FOREX"
    quote = (await get_quotes([inverse])).get(inverse)
    if quote:
        try:
            rate = float(quote["close"])
            if rate:
                return 1.0 / rate
        except (TypeError, ValueError, KeyError):
            pass

    print(f"[market] WARNING: no FX rate for {from_ccy}->{to_ccy}, using 1.0. "
          f"Values in {from_ccy} will be WRONG.")
    return 1.0


async def get_news(ticker: str | None = None, topic: str | None = None,
                   from_date: str | None = None, to_date: str | None = None,
                   limit: int = 10, offset: int = 0,
                   ccy: str | None = None) -> list[dict]:
    """
    Financial news from EODHD, with sentiment scores.

    Full parameter set per the docs:
      s      -- ticker filter (AAPL.US)
      t      -- topic tag filter (e.g. "earnings report", "merger")
      from   -- start date YYYY-MM-DD, inclusive
      to     -- end date YYYY-MM-DD, inclusive
      limit  -- 1..1000, default 50 upstream (we default to 10)
      offset -- pagination

    Supply ticker OR topic; omit both for the general feed.

    COST: each request consumes 5 API calls plus 5 per ticker -- roughly an
    order of magnitude more than a quote. Don't call this speculatively.

    TOKENS: the API returns the FULL article body in `content`. Ten articles
    unabridged is tens of thousands of tokens, so we truncate to an excerpt.
    The model gets the gist plus a link, not the whole newspaper.
    """
    params = {
        "api_token": EODHD_API_KEY,
        "fmt": "json",
        "limit": max(1, min(int(limit), 50)),  # hard cap; 1000 would be absurd here
        "offset": max(0, int(offset)),
    }
    if ticker:
        params["s"] = resolve_symbol(ticker, ccy)
    if topic:
        params["t"] = topic
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{BASE_URL}/news", params=params)
        if response.status_code != 200:
            print(f"[market] /news -> {response.status_code}: {response.text[:120]}")
            return []
        articles = response.json()
    except Exception as exc:  # noqa: BLE001
        print(f"[market] news fetch failed: {exc}")
        return []

    if not isinstance(articles, list):
        return []

    out = []
    for a in articles:
        if not isinstance(a, dict):
            continue
        body = (a.get("content") or "").strip()
        row = {
            "date": a.get("date"),
            "title": a.get("title"),
            "link": a.get("link"),
            # Excerpt only -- see the token note above.
            "excerpt": (body[:400] + "...") if len(body) > 400 else body,
        }
        # Other tickers mentioned: useful for spotting read-across to the
        # rest of the book (an Alibaba story often names Tencent too).
        symbols = a.get("symbols") or []
        if symbols:
            row["also_mentions"] = symbols[:8]
        tags = a.get("tags") or []
        if tags:
            row["tags"] = tags[:6]
        # Sentiment: polarity plus the neg/neu/pos breakdown.
        sent = a.get("sentiment") or {}
        if isinstance(sent, dict) and sent:
            row["sentiment"] = {
                "polarity": sent.get("polarity"),
                "pos": sent.get("pos"),
                "neu": sent.get("neu"),
                "neg": sent.get("neg"),
            }
        out.append(row)
    return out


async def get_sentiment(tickers: list[str], from_date: str | None = None,
                        to_date: str | None = None,
                        ccy_by_ticker: dict[str, str] | None = None) -> dict:
    """
    Daily aggregated news sentiment per ticker, normalised -1 to +1.

    Returns {ticker: [{date, count, normalized}, ...]}. `count` is how many
    articles fed that day's score -- a score built on 2 articles means far
    less than one built on 60, so always read them together.

    Accepts several tickers in one request, which matters given the per-ticker
    call cost.
    """
    ccy_by_ticker = ccy_by_ticker or {}
    symbol_to_ticker = {}
    for t in tickers:
        sym = resolve_symbol(t, ccy_by_ticker.get(t.upper()))
        symbol_to_ticker[sym] = t.upper()

    params = {
        "api_token": EODHD_API_KEY,
        "fmt": "json",
        "s": ",".join(symbol_to_ticker),
    }
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{BASE_URL}/sentiments", params=params)
        if response.status_code != 200:
            print(f"[market] /sentiments -> {response.status_code}")
            return {}
        data = response.json()
    except Exception as exc:  # noqa: BLE001
        print(f"[market] sentiment fetch failed: {exc}")
        return {}

    if not isinstance(data, dict):
        return {}

    # Re-key to the caller's plain tickers.
    return {
        symbol_to_ticker.get(sym.upper(), plain_ticker(sym)): rows
        for sym, rows in data.items()
    }


async def get_news_word_weights(ticker: str, from_date: str | None = None,
                                to_date: str | None = None, limit: int = 15,
                                ccy: str | None = None) -> dict:
    """
    Most significant words across news about a ticker in a date range.

    Useful for "what is the market actually talking about with this name" --
    thematic rather than directional.

    Note the odd parameter names: filter[date_from], filter[date_to],
    page[limit]. They differ from the other endpoints; this is per the docs,
    not a typo.

    This endpoint processes hundreds of articles with AI and can be slow.
    Narrow the date range if it times out.
    """
    symbol = resolve_symbol(ticker, ccy)
    params = {
        "api_token": EODHD_API_KEY,
        "fmt": "json",
        "s": symbol,
        "page[limit]": limit,
    }
    if from_date:
        params["filter[date_from]"] = from_date
    if to_date:
        params["filter[date_to]"] = to_date

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(
                f"{BASE_URL}/news-word-weights", params=params
            )
        if response.status_code != 200:
            print(f"[market] /news-word-weights -> {response.status_code}")
            return {}
        data = response.json()
    except Exception as exc:  # noqa: BLE001
        print(f"[market] word weights fetch failed: {exc}")
        return {}

    if not isinstance(data, dict):
        return {}
    return {
        "ticker": symbol,
        "words": data.get("data", {}),
        "articles_found": (data.get("meta") or {}).get("news_found"),
        "articles_processed": (data.get("meta") or {}).get("news_processed"),
    }


# ---------------------------------------------------------------------------
# Historical & intraday bars
#
# Both return RAW bars. The maths on top of them lives in portfolio.py, so
# the model never sees a 250-row price series -- only computed metrics.
# ---------------------------------------------------------------------------

async def get_history(ticker: str, ccy: str | None = None,
                      from_date: str | None = None,
                      to_date: str | None = None) -> list[dict]:
    """
    Daily OHLCV bars from EODHD's /eod endpoint.

    Each bar has: date, open, high, low, close, adjusted_close, volume.

    Use adjusted_close for anything return-related -- it accounts for
    dividends and splits. Using raw close would show a false -50% on any
    stock that did a 2:1 split, and understate total return on dividend
    payers like the HK banks in your book.
    """
    symbol = resolve_symbol(ticker, ccy)
    params = {"api_token": EODHD_API_KEY, "fmt": "json", "period": "d"}
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{BASE_URL}/eod/{symbol}", params=params)
        if response.status_code != 200:
            print(f"[market] /eod {symbol} -> {response.status_code}")
            return []
        bars = response.json()
    except Exception as exc:  # noqa: BLE001
        print(f"[market] history fetch failed for {symbol}: {exc}")
        return []

    return bars if isinstance(bars, list) else []


async def get_intraday(ticker: str, ccy: str | None = None,
                       interval: str = "5m",
                       lookback_hours: int = 24) -> list[dict]:
    """
    Intraday bars from EODHD's /intraday endpoint.

    interval: '1m', '5m', or '1h'. Requires the EOD+Intraday plan.
    Each bar has: timestamp, datetime, open, high, low, close, volume.

    lookback_hours defaults to 24 so a single call covers a full HK session
    plus the US session -- relevant when your book spans both.
    """
    symbol = resolve_symbol(ticker, ccy)
    now = int(time.time())
    params = {
        "api_token": EODHD_API_KEY,
        "fmt": "json",
        "interval": interval,
        "from": now - lookback_hours * 3600,
        "to": now,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{BASE_URL}/intraday/{symbol}", params=params)
        if response.status_code != 200:
            print(f"[market] /intraday {symbol} -> {response.status_code}")
            return []
        bars = response.json()
    except Exception as exc:  # noqa: BLE001
        print(f"[market] intraday fetch failed for {symbol}: {exc}")
        return []

    return bars if isinstance(bars, list) else []