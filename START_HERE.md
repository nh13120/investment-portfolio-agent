# START HERE

You don't need to know how to code to get this running. You'll be copying
commands into a terminal and pasting keys into a file. That's it.

Total time: about an hour, most of it spent creating free accounts.

---

## Step A — Put this folder somewhere sensible

Move the whole `portfolio-bot` folder to your Desktop.

Not inside "Project 1", not inside another folder. Just on the Desktop, on its
own. This makes every command below work without editing paths.

---

## Step B — Open it in VS Code

In VS Code: **File → Open Folder…** → select the `portfolio-bot` folder → Open.

If VS Code asks "Do you trust the authors of the files in this folder?" click
**Yes, I trust the authors**. (It's your own folder.)

You should now see the file list in the left sidebar: `app`, `scripts`,
`README.md`, and so on.

> If you still see "PROJECT 1" in the sidebar, you opened the wrong folder.
> Do File → Open Folder again.

---

## Step C — Open the terminal inside VS Code

Menu: **Terminal → New Terminal**.

A panel opens at the bottom. The text before the `$` should end in
`portfolio-bot`. That confirms you're in the right place.

Everything below gets typed into that panel, one line at a time, pressing
Enter after each.

---

## Step D — Run the setup script

```bash
bash setup.sh
```

This takes about a minute. It installs the four libraries the project needs
into a private folder called `.venv`, so nothing else on your Mac is affected.

When it finishes you'll see `=== Setup complete ===`.

---

## Step E — Get your five keys

This is the slow part. Open `README.md` (click it in the sidebar) and follow
**Steps 2, 3 and 4**. You need:

| Key | Where from | Time |
|---|---|---|
| `TELEGRAM_TOKEN` | @BotFather on Telegram | 2 min |
| `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` | supabase.com, free account | 10 min |
| `ANTHROPIC_API_KEY` | console.anthropic.com (add $5 credit) | 5 min |
| `TWELVE_DATA_KEY` | twelvedata.com, free account | 3 min |

**Don't skip Step 3's SQL part.** You must paste `schema.sql` into Supabase's
SQL Editor and click Run, or the bot has nowhere to store anything.

---

## Step F — Paste the keys in

In the VS Code sidebar, click the file named `.env`

> **Can't see it?** Files starting with a dot are hidden by default. In the
> VS Code sidebar, press `Cmd+Shift+P`, type "toggle excluded files", press
> Enter. It'll appear.

Replace each placeholder with your real key. It should look like:

```
TELEGRAM_TOKEN=7123456789:AAH8xK2mPqR...
ANTHROPIC_API_KEY=sk-ant-api03-x7Kd...
```

No quotes. No spaces around the `=`. Save with `Cmd+S`.

---

## Step G — Check everything works

```bash
source .venv/bin/activate
python scripts/check_setup.py
```

You get a checklist. Every line should say `[ OK ]`:

```
[ OK ]  TELEGRAM_TOKEN is set
[ OK ]  Telegram bot reachable (@your_bot)
[ OK ]  Supabase connected and tables exist
[ OK ]  Anthropic API key works
[ OK ]  Market data works (AAPL = 231.40)
```

Any `[FAIL]` line tells you exactly what to fix. Fix it, run the command again.

> `source .venv/bin/activate` must be run **once per terminal window**. If you
> close the terminal and reopen it, run it again before anything else. You'll
> know it worked because your prompt starts with `(.venv)`.

---

## Step H — Add your holdings

Click `scripts/sample_positions.csv` in the sidebar. Replace the example rows
with your own:

```
ticker,qty,avg_cost,ccy
NVDA,50,118.40,USD
0700,200,395.50,HKD
```

- `avg_cost` = what you paid per share. Leave blank if you don't know — you'll
  get the current value but not profit/loss for that row.
- `ccy` = the currency the stock trades in (USD, EUR, HKD).

Save. Then get your Telegram ID by messaging **@userinfobot** — it replies with
a number. Use it here:

```bash
python -m scripts.import_positions 123456789 scripts/sample_positions.csv
```

(Replace `123456789` with your actual number.)

---

## Step I — Start the bot

```bash
python -m app.bot
```

You'll see `Bot started. Polling for messages...`

Now open Telegram, find your bot, and **send it a direct message** (not in a
group):

```
/start
what do I hold?
how is NVDA doing?
```

That's a working AI agent querying your portfolio.

**Leave the terminal open** — closing it stops the bot. Making it run 24/7 is
README Step 8, and you should do that once the basics work.

---

## When something breaks

| What you see | What it means |
|---|---|
| `command not found: python3` | Python isn't installed → python.org/downloads |
| `No module named 'app'` | Wrong folder. `cd ~/Desktop/portfolio-bot` |
| `No module named 'anthropic'` | Forgot `source .venv/bin/activate` |
| `Missing environment variable` | A key is blank in `.env` |
| Bot ignores you | You messaged in a group. It only replies in DMs, by design. |
| `relation does not exist` | You skipped the `schema.sql` step in Supabase |

The fastest debugging move: run `python scripts/check_setup.py` again. It
isolates which of the five pieces is broken.
