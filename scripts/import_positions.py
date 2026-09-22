"""
Import holdings from a CSV into a user's portfolio.

Usage:
    python -m scripts.import_positions <telegram_user_id> path/to/positions.csv

Expected CSV columns:  ticker,qty,avg_cost,ccy
  - avg_cost may be blank. Leave it blank and the bot reports market value but
    not profit/loss for that position. Fill it in -- even approximately -- and
    P&L switches on. It's the highest-value hour you can spend on this project.
  - ccy defaults to USD if omitted.

To find your Telegram user ID: message @userinfobot on Telegram.
"""

import csv
import sys

from app import db


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    telegram_user_id = int(sys.argv[1])
    csv_path = sys.argv[2]

    portfolio_id = db.get_portfolio_id(telegram_user_id, "imported")
    imported, skipped = 0, 0

    with open(csv_path, newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            ticker = (row.get("ticker") or "").strip()
            if not ticker:
                continue

            try:
                qty = float(row["qty"])
            except (KeyError, ValueError):
                print(f"  skipped {ticker}: unreadable qty")
                skipped += 1
                continue

            raw_cost = (row.get("avg_cost") or "").strip()
            avg_cost = float(raw_cost) if raw_cost else None

            db.upsert_position(
                portfolio_id=portfolio_id,
                ticker=ticker,
                qty=qty,
                avg_cost=avg_cost,
                ccy=(row.get("ccy") or "USD").strip(),
            )
            imported += 1

    print(f"Imported {imported} positions ({skipped} skipped) "
          f"into portfolio {portfolio_id}")


if __name__ == "__main__":
    main()
