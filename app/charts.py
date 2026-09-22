"""
Chart rendering. Produces PNG bytes for Telegram.

DESIGN RULE, same as the maths: charts are drawn by THIS code from tool data.
The model decides when a chart helps and what to plot; it never writes
plotting code. A model-generated matplotlib script is a script that
occasionally plots the wrong series and looks completely convincing.

Sizing: 800x500 at 100 DPI. A chart laid out for a laptop is unreadable on a
phone, which would defeat the point of the concise formatting elsewhere.
Fonts are deliberately large for the canvas size.
"""

import io

import matplotlib

# Must be set before pyplot is imported. Agg is a headless backend -- there is
# no display on Railway, and the default backend would fail there.
matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402

# Dark palette, tuned to sit comfortably inside Telegram's dark theme.
BG = "#1a1d21"
FG = "#e8e8e8"
GRID = "#2f343a"
GREEN = "#4ade80"
RED = "#f87171"
BLUE = "#60a5fa"
AMBER = "#fbbf24"

FIGSIZE = (8, 5)
DPI = 100


def _new_figure(title: str):
    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.tick_params(colors=FG, labelsize=11)
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.grid(True, color=GRID, linewidth=0.6, alpha=0.6)
    ax.set_axisbelow(True)
    if title:
        ax.set_title(title, color=FG, fontsize=14, pad=14)
    return fig, ax


def _to_png(fig) -> bytes:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", facecolor=BG,
                bbox_inches="tight", pad_inches=0.3)
    plt.close(fig)  # release the figure or memory grows on every chart
    buffer.seek(0)
    return buffer.read()


def composition_chart(positions: list[dict], top_n: int = 12) -> bytes | None:
    """
    Horizontal bar chart of portfolio weights.

    This is the chart that earns its place: with 87 holdings, one position at
    26% sitting above a long tail of sub-1% names is obvious at a glance and
    genuinely hard to reconstruct from a list of numbers.

    Everything below top_n is collapsed into a single "others" bar, so the
    tail is represented without producing 87 illegible rows.
    """
    priced = [p for p in positions if p.get("market_value_base")]
    if len(priced) < 2:
        return None

    ranked = sorted(priced, key=lambda p: p["market_value_base"], reverse=True)
    shown = ranked[:top_n]
    tail = ranked[top_n:]

    labels = [p["ticker"] for p in shown]
    values = [p["market_value_base"] for p in shown]
    colors = [BLUE] * len(shown)

    if tail:
        labels.append(f"+{len(tail)} others")
        values.append(sum(p["market_value_base"] for p in tail))
        colors.append(GRID)

    total = sum(p["market_value_base"] for p in priced)

    # Reversed so the largest holding sits at the top.
    labels, values, colors = labels[::-1], values[::-1], colors[::-1]

    fig, ax = _new_figure("Portfolio composition")
    bars = ax.barh(labels, values, color=colors, height=0.72)
    ax.set_xlabel("Value", color=FG, fontsize=11)
    ax.grid(axis="y", visible=False)

    # Percentage labels sit outside each bar -- inside is unreadable on the
    # short ones, and the short ones are exactly the tail you want to judge.
    span = max(values) if values else 1
    for bar, value in zip(bars, values):
        pct = value / total * 100 if total else 0
        ax.text(bar.get_width() + span * 0.015,
                bar.get_y() + bar.get_height() / 2,
                f"{pct:.1f}%", va="center", color=FG, fontsize=10)

    ax.set_xlim(0, span * 1.16)  # headroom so labels aren't clipped
    return _to_png(fig)


def price_history_chart(bars: list[dict], ticker: str,
                        avg_cost: float | None = None) -> bytes | None:
    """
    Price line with the user's cost basis drawn across it.

    The cost line is the whole point. "Up 93%" is a number; seeing where you
    bought relative to the range answers "where am I on this" instantly.

    Uses adjusted_close so dividends and splits are reflected -- consistent
    with how returns are computed everywhere else.
    """
    if len(bars) < 2:
        return None

    ordered = sorted(bars, key=lambda b: b["date"])
    dates = [b["date"] for b in ordered]
    closes = [float(b.get("adjusted_close") or b["close"]) for b in ordered]

    # Colour the line by the direction of the period, not by profit.
    line_color = GREEN if closes[-1] >= closes[0] else RED

    fig, ax = _new_figure(f"{ticker} price history")
    ax.plot(dates, closes, color=line_color, linewidth=2)
    ax.fill_between(dates, closes, min(closes), color=line_color, alpha=0.10)

    if avg_cost:
        ax.axhline(avg_cost, color=AMBER, linewidth=1.4, linestyle="--")
        ax.text(0.015, avg_cost, f" your cost {avg_cost:,.2f}",
                transform=ax.get_yaxis_transform(),
                color=AMBER, fontsize=10, va="bottom")

    # Roughly 6 date labels regardless of range length, rotated to fit.
    step = max(1, len(dates) // 6)
    ax.set_xticks(dates[::step])
    ax.tick_params(axis="x", rotation=45)
    for label in ax.get_xticklabels():
        label.set_horizontalalignment("right")

    change = (closes[-1] / closes[0] - 1) * 100 if closes[0] else 0
    ax.set_title(f"{ticker}  {change:+.1f}% over period",
                 color=FG, fontsize=14, pad=14)
    return _to_png(fig)


def portfolio_value_chart(snapshots: list[dict]) -> bytes | None:
    """
    Portfolio value over time, from daily valuation snapshots.

    Returns None until at least two snapshots exist. That is not an error
    condition -- it is the honest state of a portfolio whose history is still
    being built, and the caller should say so rather than draw a flat line.
    """
    if len(snapshots) < 2:
        return None

    ordered = sorted(snapshots, key=lambda s: s["date"])
    dates = [s["date"] for s in ordered]
    values = [float(s["total_value_base"]) for s in ordered]

    line_color = GREEN if values[-1] >= values[0] else RED

    fig, ax = _new_figure("Portfolio value")
    ax.plot(dates, values, color=line_color, linewidth=2, marker="o",
            markersize=3)
    ax.fill_between(dates, values, min(values), color=line_color, alpha=0.10)

    step = max(1, len(dates) // 6)
    ax.set_xticks(dates[::step])
    ax.tick_params(axis="x", rotation=45)
    for label in ax.get_xticklabels():
        label.set_horizontalalignment("right")

    change = (values[-1] / values[0] - 1) * 100 if values[0] else 0
    ax.set_title(f"Portfolio value  {change:+.1f}%", color=FG,
                 fontsize=14, pad=14)
    return _to_png(fig)


def movers_chart(positions: list[dict], top_n: int = 10) -> bytes | None:
    """
    Today's biggest movers, up and down, as a diverging bar chart.

    Shows the largest absolute moves in either direction rather than just the
    winners -- the point is to see the day's dispersion, not a leaderboard.
    """
    moved = [p for p in positions if p.get("day_change_pct") is not None]
    if len(moved) < 2:
        return None

    ranked = sorted(moved, key=lambda p: abs(p["day_change_pct"]),
                    reverse=True)[:top_n]
    ranked.sort(key=lambda p: p["day_change_pct"])

    labels = [p["ticker"] for p in ranked]
    values = [p["day_change_pct"] for p in ranked]
    colors = [GREEN if v >= 0 else RED for v in values]

    fig, ax = _new_figure("Today's movers")
    ax.barh(labels, values, color=colors, height=0.72)
    ax.axvline(0, color=FG, linewidth=1)
    ax.set_xlabel("% change today", color=FG, fontsize=11)
    ax.grid(axis="y", visible=False)

    # Labels sit on the outside of each bar, so they never overlap the axis.
    span = max(abs(v) for v in values) or 1
    for label, value in zip(labels, values):
        offset = span * 0.03
        ax.text(value + (offset if value >= 0 else -offset), label,
                f"{value:+.1f}%", va="center",
                ha="left" if value >= 0 else "right",
                color=FG, fontsize=10)

    ax.set_xlim(-span * 1.30, span * 1.30)
    return _to_png(fig)
