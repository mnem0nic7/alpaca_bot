# Market Data Backfill — Implementation Plan

**Spec:** `docs/superpowers/specs/2026-04-26-backfill-design.md`
**Date:** 2026-04-26

---

## Overview

Add a `backfill/` module + two CLI entry points so operators can pull real Alpaca bar data to disk and run parameter sweeps against it.

Tasks are sequenced: 1 → 2 → 3 → 4 (each depends on the previous).

---

## Task 1 — `backfill/fetcher.py`

**File:** `src/alpaca_bot/backfill/fetcher.py` (new)
**File:** `src/alpaca_bot/backfill/__init__.py` (new, empty)

```python
from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alpaca_bot.config import Settings
from alpaca_bot.execution.alpaca import AlpacaMarketDataAdapter

logger = logging.getLogger(__name__)


class BackfillFetcher:
    def __init__(self, adapter: AlpacaMarketDataAdapter, settings: Settings) -> None:
        self._adapter = adapter
        self._settings = settings

    def fetch_and_save(
        self,
        *,
        symbols: Sequence[str],
        days: int,
        output_dir: Path,
        starting_equity: float = 100_000.0,
    ) -> list[tuple[Path, int, int]]:
        """Fetch and write scenario files. Returns list of (path, n_intraday, n_daily)."""
        output_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(tz=timezone.utc)
        end = now
        # days is trading days; multiply by 1.5 to get enough calendar days
        calendar_days = int(days * 1.5) + 14
        start = now - timedelta(days=calendar_days)

        daily_by_symbol = self._adapter.get_daily_bars(
            symbols=list(symbols), start=start, end=end
        )
        intraday_by_symbol = self._adapter.get_stock_bars(
            symbols=list(symbols), start=start, end=end, timeframe_minutes=15
        )

        results: list[tuple[Path, int, int]] = []
        for symbol in symbols:
            daily = daily_by_symbol.get(symbol, [])
            intraday = intraday_by_symbol.get(symbol, [])
            if not daily or not intraday:
                logger.warning("No bars returned for %s — skipping", symbol)
                continue

            payload = {
                "name": f"{symbol}_{days}d",
                "symbol": symbol,
                "starting_equity": starting_equity,
                "daily_bars": [_bar_to_dict(b) for b in daily],
                "intraday_bars": [_bar_to_dict(b) for b in intraday],
            }
            path = output_dir / f"{symbol}_{days}d.json"
            path.write_text(json.dumps(payload, indent=2))
            logger.info("Wrote %s (%d intraday, %d daily bars)", path, len(intraday), len(daily))
            results.append((path, len(intraday), len(daily)))

        return results


def _bar_to_dict(bar: "Bar") -> dict:
    return {
        "symbol": bar.symbol,
        "timestamp": bar.timestamp.isoformat(),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
    }
```

**Test command:** `pytest tests/unit/test_backfill_fetcher.py -v`

---

## Task 2 — `tests/unit/test_backfill_fetcher.py`

**File:** `tests/unit/test_backfill_fetcher.py` (new)

```python
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from alpaca_bot.backfill.fetcher import BackfillFetcher
from alpaca_bot.domain.models import Bar
from alpaca_bot.replay.runner import ReplayRunner


def _make_settings(**overrides):
    from alpaca_bot.config import Settings, TradingMode, MarketDataFeed
    from datetime import time
    defaults = dict(
        trading_mode=TradingMode.PAPER,
        enable_live_trading=False,
        strategy_version="v1",
        database_url="postgresql://localhost/test",
        market_data_feed=MarketDataFeed.SIP,
        symbols=("AAPL",),
        daily_sma_period=5,
        breakout_lookback_bars=5,
        relative_volume_lookback_bars=5,
        relative_volume_threshold=1.5,
        entry_timeframe_minutes=15,
        risk_per_trade_pct=0.01,
        max_position_pct=0.1,
        max_open_positions=3,
        daily_loss_limit_pct=0.01,
        stop_limit_buffer_pct=0.001,
        breakout_stop_buffer_pct=0.001,
        entry_stop_price_buffer=0.01,
        entry_window_start=time(10, 0),
        entry_window_end=time(15, 30),
        flatten_time=time(15, 45),
        prior_day_high_lookback_bars=1,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _make_bar(symbol: str, ts: datetime, price: float = 100.0) -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=ts,
        open=price - 0.5,
        high=price + 1.0,
        low=price - 1.0,
        close=price,
        volume=10_000.0,
    )


class FakeAdapter:
    def __init__(self, daily: dict, intraday: dict):
        self._daily = daily
        self._intraday = intraday

    def get_daily_bars(self, *, symbols, start, end):
        return {s: self._daily.get(s, []) for s in symbols}

    def get_stock_bars(self, *, symbols, start, end, timeframe_minutes):
        return {s: self._intraday.get(s, []) for s in symbols}


_TS = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)
_DAILY_BARS = [_make_bar("AAPL", _TS, 150.0 + i) for i in range(5)]
_INTRADAY_BARS = [_make_bar("AAPL", _TS, 151.0 + i * 0.1) for i in range(20)]


def test_fetch_writes_one_file_per_symbol(tmp_path):
    adapter = FakeAdapter(
        daily={"AAPL": _DAILY_BARS, "MSFT": [_make_bar("MSFT", _TS)]},
        intraday={"AAPL": _INTRADAY_BARS, "MSFT": [_make_bar("MSFT", _TS)]},
    )
    settings = _make_settings(symbols=("AAPL", "MSFT"))
    fetcher = BackfillFetcher(adapter, settings)
    results = fetcher.fetch_and_save(symbols=["AAPL", "MSFT"], days=10, output_dir=tmp_path)
    assert len(results) == 2
    assert (tmp_path / "AAPL_10d.json").exists()
    assert (tmp_path / "MSFT_10d.json").exists()


def test_fetch_file_is_loadable_by_replay_runner(tmp_path):
    adapter = FakeAdapter(daily={"AAPL": _DAILY_BARS}, intraday={"AAPL": _INTRADAY_BARS})
    settings = _make_settings()
    fetcher = BackfillFetcher(adapter, settings)
    fetcher.fetch_and_save(symbols=["AAPL"], days=252, output_dir=tmp_path)
    scenario = ReplayRunner.load_scenario(tmp_path / "AAPL_252d.json")
    assert scenario.symbol == "AAPL"
    assert len(scenario.daily_bars) == 5
    assert len(scenario.intraday_bars) == 20


def test_fetch_skips_symbol_with_no_bars(tmp_path):
    adapter = FakeAdapter(daily={}, intraday={})
    settings = _make_settings()
    fetcher = BackfillFetcher(adapter, settings)
    results = fetcher.fetch_and_save(symbols=["AAPL"], days=10, output_dir=tmp_path)
    assert results == []
    assert not (tmp_path / "AAPL_10d.json").exists()


def test_fetch_filename_convention(tmp_path):
    adapter = FakeAdapter(daily={"AAPL": _DAILY_BARS}, intraday={"AAPL": _INTRADAY_BARS})
    settings = _make_settings()
    fetcher = BackfillFetcher(adapter, settings)
    [(path, _, _)] = fetcher.fetch_and_save(symbols=["AAPL"], days=90, output_dir=tmp_path)
    assert path.name == "AAPL_90d.json"


def test_fetch_respects_output_dir(tmp_path):
    sub = tmp_path / "custom" / "dir"
    adapter = FakeAdapter(daily={"AAPL": _DAILY_BARS}, intraday={"AAPL": _INTRADAY_BARS})
    settings = _make_settings()
    fetcher = BackfillFetcher(adapter, settings)
    [(path, _, _)] = fetcher.fetch_and_save(symbols=["AAPL"], days=10, output_dir=sub)
    assert path.parent == sub
    assert sub.exists()
```

**Test command:** `pytest tests/unit/test_backfill_fetcher.py -v`

---

## Task 3 — `backfill/cli.py` (alpaca-bot-backfill)

**File:** `src/alpaca_bot/backfill/cli.py` (new)

```python
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from alpaca_bot.backfill.fetcher import BackfillFetcher
from alpaca_bot.config import Settings
from alpaca_bot.execution.alpaca import AlpacaMarketDataAdapter, AlpacaCredentialsError

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch Alpaca bar data and write ReplayScenario JSON files."
    )
    parser.add_argument(
        "--symbols", nargs="+", metavar="SYM",
        help="Symbols to fetch (default: settings.symbols from env)",
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
        help="starting_equity field in each scenario file (default: 100000)",
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
```

**Test command:** `pytest tests/unit/test_backfill_fetcher.py -v` (CLI is thin glue; coverage from fetcher tests)

---

## Task 4 — `tuning/sweep_cli.py` (alpaca-bot-sweep)

**File:** `src/alpaca_bot/tuning/sweep_cli.py` (new)

```python
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from alpaca_bot.replay.runner import ReplayRunner
from alpaca_bot.tuning.sweep import DEFAULT_GRID, ParameterGrid, run_sweep


def _parse_grid(specs: list[str]) -> ParameterGrid:
    grid: ParameterGrid = {}
    for spec in specs:
        key, _, values = spec.partition("=")
        if not key or not values:
            sys.exit(f"Invalid --grid spec: {spec!r}. Expected KEY=v1,v2,...")
        grid[key.strip()] = [v.strip() for v in values.split(",")]
    return grid


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run parameter sweep over backfill scenario files."
    )
    parser.add_argument(
        "--scenario-dir", default="data/backfill",
        help="Directory containing *.json scenario files (default: data/backfill)",
    )
    parser.add_argument(
        "--min-trades", type=int, default=3,
        help="Minimum trades to score a candidate (default: 3)",
    )
    parser.add_argument(
        "--grid", nargs="*", default=[],
        metavar="KEY=v1,v2,...",
        help="Grid overrides, e.g. BREAKOUT_LOOKBACK_BARS=15,20,25",
    )
    args = parser.parse_args()

    grid = _parse_grid(args.grid) if args.grid else DEFAULT_GRID

    scenario_dir = Path(args.scenario_dir)
    files = sorted(scenario_dir.glob("*.json"))
    if not files:
        sys.exit(f"No *.json files found in {scenario_dir}")

    base_env = dict(os.environ)

    for fpath in files:
        print(f"\n=== {fpath.name} ===")
        scenario = ReplayRunner.load_scenario(fpath)
        candidates = run_sweep(
            scenario=scenario,
            base_env=base_env,
            grid=grid,
            min_trades=args.min_trades,
        )
        top = [c for c in candidates if c.score is not None][:10]
        if not top:
            print("  No scored candidates (all disqualified — fewer than min_trades).")
            continue
        print(f"  {'Rank':<5} {'Score':>8}  {'Trades':>6}  {'MeanRet':>8}  Params")
        for rank, c in enumerate(top, 1):
            report = c.report
            trades = report.total_trades if report else "?"
            mean_ret = f"{report.mean_return_pct:.2f}%" if report and report.mean_return_pct is not None else "n/a"
            params_str = " ".join(f"{k}={v}" for k, v in c.params.items())
            print(f"  {rank:<5} {c.score:>8.4f}  {trades:>6}  {mean_ret:>8}  {params_str}")
```

**Test command:** `pytest tests/unit/test_backfill_fetcher.py -v && pytest -q`

---

## Task 5 — Wire entry points into `pyproject.toml`

**File:** `pyproject.toml`

Find the `[project.scripts]` section and add:

```toml
alpaca-bot-backfill = "alpaca_bot.backfill.cli:main"
alpaca-bot-sweep    = "alpaca_bot.tuning.sweep_cli:main"
```

Then reinstall: `pip install -e ".[dev]"`

**Test command:** `alpaca-bot-backfill --help && alpaca-bot-sweep --help`

---

## Task 6 — Full test suite

**Test command:** `pytest -q`

All 398+ tests must pass. No new failures.

---

## Implementation Notes

- `BackfillFetcher` takes `AlpacaMarketDataAdapter` via constructor injection — tests pass a `FakeAdapter` with the same duck-typed interface.
- `_bar_to_dict` uses attribute access (not `dataclasses.asdict`) to stay compatible with the existing `Bar` type regardless of whether it's a dataclass or model.
- The sweep CLI reads `os.environ` directly as `base_env` so it picks up all Settings fields, including credentials and the strategy version.
- The `data/backfill/` directory is relative to the current working directory when the CLI runs — operators should run from the project root.
- No `BACKFILL_OUTPUT_DIR` Settings field: the output directory is a CLI argument, not a settings concern (it's a one-shot research tool, not a running service).
