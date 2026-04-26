from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from alpaca_bot.backfill.fetcher import BackfillFetcher
from alpaca_bot.config import Settings
from alpaca_bot.execution.alpaca import AlpacaCredentialsError, AlpacaMarketDataAdapter

logging.basicConfig(level=logging.INFO, format="%(message)s")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch Alpaca bar data and write ReplayScenario JSON files."
    )
    parser.add_argument(
        "--symbols", nargs="+", metavar="SYM",
        help="Symbols to fetch (default: SYMBOLS from env / settings.symbols)",
    )
    parser.add_argument(
        "--days", type=int, default=252,
        help="Trading days of history to fetch (default: 252)",
    )
    parser.add_argument(
        "--output-dir", default="data/backfill",
        help="Directory to write JSON files (default: data/backfill)",
    )
    parser.add_argument(
        "--equity", type=float, default=100_000.0,
        help="starting_equity in each scenario file (default: 100000)",
    )
    args = parser.parse_args()

    try:
        settings = Settings.from_env(dict(os.environ))
    except ValueError as exc:
        sys.exit(f"Configuration error: {exc}")

    symbols = list(args.symbols) if args.symbols else list(settings.symbols)
    if not symbols:
        sys.exit("No symbols specified and SYMBOLS is not set in the environment.")

    try:
        adapter = AlpacaMarketDataAdapter.from_settings(settings)
    except AlpacaCredentialsError as exc:
        sys.exit(f"Alpaca credentials error: {exc}")

    fetcher = BackfillFetcher(adapter, settings)
    output_dir = Path(args.output_dir)

    results = fetcher.fetch_and_save(
        symbols=symbols,
        days=args.days,
        output_dir=output_dir,
        starting_equity=args.equity,
    )

    for path, n_intraday, n_daily in results:
        print(f"Wrote {path} ({n_intraday} intraday, {n_daily} daily bars)")

    if not results:
        sys.exit("No files written — check symbols and credentials.")
