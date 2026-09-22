# Portfolio Bot — Build Guide

A Telegram bot that answers questions about your stock portfolio and fires alerts.

Work through the steps in order. **Steps 1–6 get you a working bot on your laptop
in about an hour.** Steps 7–9 make it run 24/7. Don't skip ahead — each step
gives you something testable, so when it breaks you know exactly what broke.

---

## How the pieces fit

```
You (Telegram)
      |
      v
Telegram servers  ──── forward your message ────►  Your Python process
                                                          |
                                                    ┌─────┴─────┐
                                                    v           v
                                              Claude API    Supabase
                                              (decides      (holdings,
                                               which tool)   snapshots)
                                                    |
                                                    v
                                              Twelve Data
                                              (live prices)
```

The one non-obvious idea: **Claude never touches your database and never does
arithmetic.** It reads your question, picks a tool, and your Python code runs
that tool and computes every number. Claude only decides *what to ask for* and
writes the sentence around the result.

This matters because language models are excellent at intent and unreliable at
maths. A model that "helpfully" estimates your annual return will produce a
number that looks right and isn't.

---

## Step 1 — Install Python and set up the project

You need Python 3.11 or newer. Check:

```bash
python3 --version
```

Then, from inside the `portfolio-bot` folder:

```bash
python3 -m venv .venv           # isolated environment for this project
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

You'll know it worked because your terminal prompt now starts with `(.venv)`.
Re-run `source .venv/bin/activate` every time you open a new terminal.

---

## Step 2 — Create the Telegram bot

1. Open Telegram, search for **@BotFather**, start a chat.
2. Send `/newbot`.
3. Give it a display name (anything) and a username (must end in `bot`).
4. BotFather replies with a token like `7123456789:AAH8x...`. **That token is a
   password** — anyone with it controls your bot.

Also get your own numeric Telegram user ID: message **@userinfobot** and it
replies with a number. Save it, you need it in Step 5.

---

## Step 3 — Create the Supabase database

1. Sign up at [supabase.com](https://supabase.com), create a project (free tier
   is plenty). Pick a region near you — Singapore or Hong Kong.
2. Left sidebar → **SQL Editor** → **New query**.
3. Paste the entire contents of `schema.sql`, hit **Run**. You should see
   "Success. No rows returned."
4. Go to **Project Settings → API** and copy two values:
   - **Project URL** → `SUPABASE_URL`
   - **service_role** key (under "Project API keys", click reveal) → `SUPABASE_SERVICE_KEY`

> The service_role key bypasses all database security. It belongs in your `.env`
> and on your server, and nowhere else — not in the repo, not in a screenshot,
> not in a message to a friend.

---

## Step 4 — Get the remaining two keys

**Anthropic:** [console.anthropic.com](https://console.anthropic.com) → API Keys
→ Create Key. Add $5 of credit; at your usage that lasts a long time.

**Twelve Data:** [twelvedata.com](https://twelvedata.com) → sign up → copy the
API key from the dashboard. The free tier allows ~8 requests/minute, which is
why `market.py` batches and caches rather than fetching one ticker at a time.

Now create your `.env`:

```bash
cp .env.example .env
```

Open `.env` and paste in all five values. No quotes, no spaces around the `=`.

---

## Step 5 — Load your positions

Edit `scripts/sample_positions.csv` — or export from your broker and reshape it
to these columns:

```csv
ticker,qty,avg_cost,ccy
NVDA,50,118.40,USD
MC,12,585.00,EUR
0700,200,395.50,HKD
ASML,15,,EUR
```

`avg_cost` can be blank (see `ASML` above) — you'll get market value but no
profit/loss for that row. **Fill these in wherever you can.** It's the single
highest-return hour of work in this project: it's what turns "you hold 50 NVDA
worth $9,200" into "you're up 55% on NVDA."

Import:

```bash
python -m scripts.import_positions <your_telegram_user_id> scripts/sample_positions.csv
```

Check it landed: Supabase → **Table Editor** → `positions`.

---

## Step 6 — Run it

```bash
python -m app.bot
```

You should see `Bot started. Polling for messages...`

Now open Telegram, find your bot by its username, and **send it a direct message**:

```
/start
what do I hold?
how is NVDA doing?
what are my three biggest positions?
alert me if NVDA goes below 140
```

Watch the terminal while you do this — you'll see the tool calls happening.
That's the agent loop working.

**If it doesn't respond**, check the terminal for the error. The three usual
causes: a typo in `.env`, the schema didn't run, or you messaged in a group
instead of a DM.

---

## Step 7 — Start recording valuations (do this today)

```bash
python -m app.jobs snapshot
```

This writes one row to `valuation_snaps`: today's total value.

**Why this is urgent.** You have current holdings but no transaction history,
which means return figures are impossible today — there's no "before" to
compare against. Each daily snapshot adds one data point. Two snapshots and you
can compute a return; sixty and you can annualise it.

Every day you delay is a permanent hole in your track record. Run it now, then
automate it in Step 9.

Ask the bot "what's my return this month?" before you have history and it will
tell you plainly that it can't compute one yet. That's the design working — it
refuses rather than inventing.

---

## Step 8 — Deploy so it runs without your laptop

Right now, closing your terminal kills the bot. To fix that:

1. Push to a **private** GitHub repo. Confirm `.env` is absent — `.gitignore`
   covers it, but check.
2. [railway.app](https://railway.app) → New Project → Deploy from GitHub repo.
3. **Variables** tab → add all five environment variables from your `.env`.
4. **Settings → Deploy → Start Command:** `python -m app.bot`

That's it. Polling works fine on Railway and needs no public URL — you can stay
on it indefinitely at this scale. Cost is roughly $5/month.

Once deployed, the bot answers from your phone in Paris while your laptop is
shut in Hong Kong.

*(Webhooks are faster and cheaper at high volume, but add HTTPS setup and a web
server for no benefit at four users. Skip them.)*

---

## Step 9 — Schedule the recurring jobs

In Railway, add two **Cron** services pointing at the same repo:

| Command | Schedule | What it does |
|---|---|---|
| `python -m app.jobs snapshot` | `0 21 * * 1-5` | Daily value, after US close |
| `python -m app.jobs alerts` | `*/15 * * * *` | Check price alerts |

Cron times are UTC. `0 21` is 21:00 UTC ≈ 5pm New York.

Alerts have a 12-hour cooldown per rule (`ALERT_COOLDOWN_HOURS` in `jobs.py`) —
without it, a stock sitting just under your threshold pings you every 15 minutes
and you mute the bot by lunchtime.

---

## What's in each file

| File | Purpose |
|---|---|
| `app/config.py` | Loads env vars, crashes early with a clear message if one's missing |
| `app/telegram.py` | Send/receive wrapper. Handles the 4096-char limit |
| `app/db.py` | Every Supabase query. Security boundary lives here |
| `app/market.py` | Twelve Data prices, batched + 60s cache, FX conversion |
| `app/portfolio.py` | **All maths.** Valuation, P&L, time-weighted return, drawdown |
| `app/tools.py` | The five tools Claude can call, and the dispatcher |
| `app/agent.py` | The agent loop |
| `app/handlers.py` | Routing, slash commands, the DM-only privacy guard |
| `app/bot.py` | Entry point |
| `app/jobs.py` | Snapshot + alert cron jobs |
| `schema.sql` | Database tables |

---

## Two things to understand before you extend it

**The privacy guard.** You have per-user portfolios in a shared group. If the
bot answered portfolio questions in the group, one person's positions and P&L
would sit in everyone's scrollback forever. `handlers.py` checks
`chat["type"] == "private"` *before* resolving any portfolio. Don't move that
check, and don't try to solve it by asking the model to be discreet in the
prompt — prompts are suggestions, code is a guarantee.

**Adding a tool.** Three edits, always in this order:

1. Write the function in `portfolio.py` or `market.py` — pure Python, no model.
2. Add its schema to `TOOL_SCHEMAS` in `tools.py`. The `description` field is
   what Claude reads to decide when to call it, so write it as instructions to
   a colleague, not as documentation.
3. Add a branch in `dispatch()`.

Nothing in `agent.py` changes. That's the payoff of the tool architecture — new
capabilities are additive and the loop stays fixed.

---

## Where to take it next

- **Backfill cost basis** — biggest immediate win, unlocks all P&L.
- **Benchmark comparison** — add a `compare_to_benchmark` tool pulling SPY/HSI
  over the same window. Turns "up 3%" into "up 3% vs SPY's 5%", which is the
  number that actually matters.
- **Broker sync** — if you're on IBKR, a Flex Query exports full trade history
  as CSV. That eliminates the snapshot workaround entirely and gives you real
  returns retroactively.
- **Semantic news alerts** — LLM scores headlines for materiality against your
  holdings. Build this *last*: it's the most expensive per run and worthless
  until you know which tickers people actually care about.
