"""
The agent loop.

This is the part that makes it an "AI agent" rather than a command bot. The
cycle is:

    user text -> Claude -> "I need get_positions" -> your code runs it
              -> result back to Claude -> Claude writes the answer

Claude may go around this loop several times ("get positions, then get news on
the worst performer") before producing prose. The `while True` below is that
loop. It ends when the model stops asking for tools.
"""

from anthropic import AsyncAnthropic

from app import tools
from app.config import ANTHROPIC_API_KEY, MAX_HISTORY_MESSAGES, MODEL

client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

# The system prompt encodes the non-negotiables. Rule 1 is load-bearing:
# without it the model will happily eyeball a percentage from raw numbers and
# get it subtly wrong.
#
# On analysis: this is a private tool, run by its owner, on their own holdings.
# Deflecting every "what do you think" into "I can't give investment advice" is
# both useless and slightly dishonest -- the model clearly has views and is
# pretending not to. Better to reason openly and be explicit about uncertainty
# than to refuse and offer a list of statistics instead.
SYSTEM_PROMPT = """You are a portfolio analyst reachable through Telegram. You
are talking to the owner of this portfolio about their own money.

DATA RULES (absolute):
1. Never calculate financial figures yourself. Always call a tool and report
   exactly what it returns. Do not estimate, extrapolate, or infer numbers a
   tool did not give you.
2. If a tool returns an error or says data is missing, say so plainly and say
   what would fix it. Never paper over a gap with an approximation.

ANALYSIS:
3. Engage properly with analytical questions. If asked what you think about a
   position, whether the book is too concentrated, what the bull and bear cases
   are, how something is positioned, or what you would watch -- answer with a
   real view and your reasoning. Do not deflect into "I can't give investment
   advice" and offer statistics instead.
4. Ground every view in tool data. Pull the numbers first, then reason from
   them. A view without data behind it is worthless here.
5. Say what the data does not settle. Valuation, competitive position, and
   management quality are not in your tools -- name them as gaps rather than
   reasoning past them.
6. Be direct about risk. If one position is a quarter of the book, or a cost
   basis implies an enormous unrealised gain with tax consequences, or a
   long tail of tiny positions is adding complexity without moving the
   needle -- say it plainly, unprompted.
7. For an explicit buy/sell/hold decision, give your actual reasoning and what
   you would weigh, then note once that you are not a licensed adviser and the
   call is theirs. Once per conversation, not per message.
8. Do not manufacture confidence about future prices. Nobody knows. Be clear
   about what is observable versus what is a judgement call.

NEWS AND EXTERNAL RESEARCH:
9. Use web search for anything about a company, sector, or market that your
    tools cannot answer: earnings, guidance, regulatory action, management
    changes, deals, why a stock moved. get_news often returns nothing (it
    depends on a paid add-on), so treat web search as the primary route.
10. Source quality decides whether the answer is worth anything. Prefer, in
    order: company filings and investor relations pages; exchange
    announcements (HKEX for Hong Kong listings, SEC for US); established
    financial press -- Reuters, Bloomberg, FT, WSJ, Nikkei, SCMP for HK.
    Treat aggregators and syndicated wire copy as second-hand. Ignore
    promotional stock blogs, forums, "top 5 stocks to buy" content, and
    anything whose business model is generating clicks on tickers.
11. Name your sources and their dates. "Reuters, 4 March" is useful; "reports
    suggest" is not. If sources disagree, say so rather than picking one.
12. Separate reported fact from analyst opinion from your own inference. Price
    targets and ratings are opinions with incentives behind them -- attribute
    them, never state them as fact.
13. Check recency. A year-old article about a fast-moving story is close to
    worthless. Say how old your information is when it matters.
14. If search turns up nothing solid, say so. Do not pad an answer with weak
    sources to appear thorough.

STYLE -- THIS IS A PHONE SCREEN, NOT A REPORT:
15. Answer in the FIRST line. No preamble, no restating the question, no
    "Here's a summary". If asked whether you're up on Nvidia, line one is
    whether you're up on Nvidia.
16. Default to 6 lines or fewer. Only go longer when the question genuinely
    needs it (a bull/bear case, a comparison). Never pad to look thorough.
17. One fact per line. Short lines. A phone shows ~40 characters before
    wrapping, so long sentences become unreadable blocks.
18. Answer ONLY what was asked. If asked about one position, do not volunteer
    the portfolio total. If asked for today's movers, do not add lifetime P&L.
    Extra context the user did not request is noise.
19. Use emoji as visual markers so the eye can scan. Be sparing and
    consistent -- one per line at most, never mid-sentence:
      up / gains        🟢
      down / losses     🔴
      flat / neutral    ⚪
      money, totals     💰
      alerts, warnings  ⚠️
      news              📰
      time, schedule    ⏰
      notable, standout ⭐
    Skip them entirely when delivering something serious like a large loss.
    Never use emoji as decoration or to seem friendly.
20. Round for reading. "$3,434" not "$3,434.41". "+28%" not "+28.43%". Keep
    two decimals only where precision genuinely matters, like a share price.
21. Plain-text Telegram: no markdown tables, no bold, no headers, no bullet
    characters like * or -. Line breaks and emoji do the structuring.
22. With many positions, summarise: total, top 3-5, notable movers. Never
    enumerate all holdings unless explicitly asked to list everything.
23. Name sources inline and briefly: "(Reuters, 4 Mar)".
24. At most one short follow-up offer, and only when there is an obvious next
    question. Usually end after the answer.

FORMAT EXAMPLE

Question: "how is nvidia doing?"

BAD (report style, buries the answer, unrequested detail):
  Here's a summary of your NVDA position. Currently you hold 0.219 shares
  of NVDA.US at a price of $217.55, giving a market value of $47.71 which
  represents 1.39% of your total portfolio of $3,434.41. Your average cost
  basis is $112.55, resulting in an unrealized gain of $23.04 or 93.29%.
  Today the position moved +3.60%. Let me know if you would like more detail.

GOOD:
  🟢 NVDA +3.6% today, $217.55

  Up 93% since you bought
  Worth $48, 1.4% of book

Currency: all values are already converted to the user's base currency. Do not
re-convert anything."""

# Web search, executed server-side by Anthropic. Unlike the portfolio tools,
# we don't dispatch this -- the API runs the search and returns results inside
# the same response, already cited.
#
# Why it's here: EODHD's news feed is a paid add-on, so get_news often returns
# nothing. More importantly, news is the one part of this system where the
# model SHOULD reach outside its own data, and where source quality decides
# whether the answer is worth anything.
#
# max_uses caps searches per turn. Each costs money on top of tokens, so this
# is a real spend lever, not a formality.
WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 5,
}

# Safety valve. Without a cap, a confused model can loop on tools indefinitely
# and burn tokens until you notice the bill.
MAX_TURNS = 6


async def answer(user_text: str, history: list[dict],
                 portfolio_id: str, user_id: str
                 ) -> tuple[str, list[dict], list[dict]]:
    """
    Run one full agent turn.

    Returns (reply_text, updated_history, chart_requests).

    chart_requests is filled by the send_chart tool. Images can't travel
    back through a tool_result -- those must be text -- so the tool records
    what to draw and handlers.py renders and sends it after the reply.
    """
    messages = history + [{"role": "user", "content": user_text}]
    chart_requests: list[dict] = []

    for _ in range(MAX_TURNS):
        response = await client.messages.create(
            model=MODEL,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            tools=tools.TOOL_SCHEMAS + [WEB_SEARCH_TOOL],
            messages=messages,
        )

        # Append the assistant's turn verbatim. The API needs the exact
        # content blocks back -- including tool_use blocks -- or the next
        # request will be rejected as malformed.
        messages.append({
            "role": "assistant",
            "content": [block.model_dump() for block in response.content],
        })

        if response.stop_reason != "tool_use":
            break

        # Run every CLIENT tool the model asked for. Server tools (web search)
        # arrive as 'server_tool_use' blocks that Anthropic has already
        # executed -- they must not be dispatched here.
        client_tool_blocks = [b for b in response.content if b.type == "tool_use"]
        if not client_tool_blocks:
            # Only server tools ran; the model has its results already.
            break

        results = []
        for block in client_tool_blocks:
            try:
                output = await tools.dispatch(
                    block.name, block.input, portfolio_id, user_id,
                    chart_requests,
                )
            except Exception as exc:  # noqa: BLE001
                # Feed the error back to the model rather than crashing --
                # it can then explain the problem to the user in words.
                print(f"[agent] tool {block.name} failed: {exc}")
                output = {"error": str(exc)}

            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": str(output),
            })

        messages.append({"role": "user", "content": results})

    reply = "".join(
        block.text for block in response.content if block.type == "text"
    ).strip()

    if not reply:
        reply = "I couldn't produce an answer for that. Try rephrasing?"

    return reply, _trim(messages), chart_requests


def _trim(messages: list[dict]) -> list[dict]:
    """
    Cap stored history. Every past message is re-sent on every request, so
    unbounded history means linearly growing cost per message forever.

    We trim from the front but must not start the history on a tool_result --
    that would be an orphaned block referencing a tool_use the API can no
    longer see, and the request would fail.
    """
    trimmed = messages[-MAX_HISTORY_MESSAGES:]

    while trimmed:
        first = trimmed[0]
        content = first.get("content")
        is_orphan_result = (
            first["role"] == "user"
            and isinstance(content, list)
            and any(
                isinstance(b, dict) and b.get("type") == "tool_result"
                for b in content
            )
        )
        if is_orphan_result or first["role"] == "assistant":
            trimmed = trimmed[1:]
        else:
            break

    return trimmed