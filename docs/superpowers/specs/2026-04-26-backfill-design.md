# Market Data Backfill System — Design Spec

**Date:** 2026-04-26
**Status:** Approved

---

## Problem

The replay runner (`replay/runner.py`) loads `ReplayScenario` objects from JSON/YAML files that embed bar arrays inline. There is no mechanism to pull real historical data from Alpaca's REST API into these files. As a result, `tuning/sweep.py` — which runs parameter grid sweeps over scenarios — is only usable with hand-crafted synthetic data, making it useless for real research.

---

## Solution Overview

A new `backfill/` module that:
1. Fetches historical daily + 15-min bars from Alpaca via the existing `AlpacaMarketDataAdapter`
2. Serialises them as `ReplayScenario`-compatible JSON files (one per symbol)
3. Exposes a `alpaca-bot-backfill` CLI entry point
4. Is consumed by a new `alpaca-bot-sweep` CLI entry point that runs the parameter sweep over a directory of backfill files

No new Postgres tables. Pure file I/O.

---

## File Format

**JSON** — matches the existing scenario format that `ReplayRunner.load_scenario()` already parses. No new dependencies (no Parquet, no pandas). Human-readable and inspectable with standard tools.

Output filename convention: `{SYMBOL}_{days}d.json`

Example: `data/backfill/AAPL_252d.json`

Schema — identical to `ReplayScenario` serialised form:

```json
{
  "name": "AAPL_252d",
  "symbol": "AAPL",
  "starting_equity": 100000.0,
  "daily_bars": [...],
  "intraday_bars": [...]
}
```

Each bar element: `{"symbol", "timestamp", "open", "high", "low", "close", "volume"}`.
Timestamps are ISO-8601 strings (what `Bar.from_dict()` already parses).

---

## Architecture

```
backfill/
  __init__.py          (empty)
  fetcher.py           BackfillFetcher — fetches + serialises
  cli.py               alpaca-bot-backfill entry point
```

`tuning/`
  `sweep_cli.py`       alpaca-bot-sweep entry point (new file)

### BackfillFetcher

```python
class BackfillFetcher:
    def __init__(self, adapter: AlpacaMarketDataAdapter, settings: Settings): ...

    def fetch_and_save(
        self,
        *,
        symbols: Sequence[str],
        days: int,
        output_dir: Path,
        starting_equity: float = 100_000.0,
    ) -> list[Path]:
        """Fetch bars for each symbol and write one JSON file per symbol.
        Returns list of written paths."""
```

The fetcher:
- Computes `start = today - timedelta(days=days + 10)` (buffer for non-trading days), `end = today`
- Calls `adapter.get_daily_bars(symbols, start, end)` and `adapter.get_stock_bars(symbols, start, end, timeframe_minutes=15)`
- Serialises a `ReplayScenario`-shaped dict and writes `output_dir/{SYMBOL}_{days}d.json`
- Logs a warning and skips any symbol with 0 bars returned

### CLI: `alpaca-bot-backfill`

```
alpaca-bot-backfill [--symbols AAPL MSFT ...] [--days 252] [--output-dir data/backfill/] [--equity 100000]
```

- Defaults: `--symbols` from `settings.symbols`, `--days 252`, `--output-dir data/backfill/`, `--equity 100000`
- Reads credentials from env (paper keys via `Settings.from_env(os.environ)`)
- Creates output dir if missing
- Prints one line per written file: `Wrote data/backfill/AAPL_252d.json (1508 intraday, 252 daily bars)`

### CLI: `alpaca-bot-sweep`

```
alpaca-bot-sweep [--scenario-dir data/backfill/] [--min-trades 3] [--grid BREAKOUT_LOOKBACK_BARS=15,20,25]
```

- Loads every `*.json` file in `--scenario-dir` as a `ReplayScenario`
- For each scenario, runs `run_sweep(scenario, base_env, grid, min_trades)` using `os.environ` as `base_env`
- Prints a ranked table of top-10 candidates per scenario (Sharpe + total trades + mean return)
- `--grid` is zero or more `KEY=v1,v2,...` overrides; defaults to `DEFAULT_GRID` from `tuning/sweep.py`

---

## Settings Integration

No new Settings fields. The backfill CLI uses `Settings.from_env(os.environ)` to get credentials and the symbol universe. Paper keys (`ALPACA_PAPER_API_KEY` / `ALPACA_PAPER_SECRET_KEY`) are used — backfill is always a read-only market data operation; live keys are not needed.

---

## Entry Points (pyproject.toml)

```toml
[project.scripts]
alpaca-bot-backfill = "alpaca_bot.backfill.cli:main"
alpaca-bot-sweep    = "alpaca_bot.tuning.sweep_cli:main"
```

---

## Error Handling

- Missing credentials → clear error message: "ALPACA_PAPER_API_KEY and ALPACA_PAPER_SECRET_KEY must be set"
- Alpaca API error → log and exit with non-zero status; do not write partial files
- Output directory creation failure → propagate `OSError`
- Symbol returns 0 bars → warn and skip (do not write empty file)

---

## Testing

Unit tests only (no live API calls). Strategy: inject a fake `AlpacaMarketDataAdapter` that returns pre-canned `dict[str, list[Bar]]`.

Test file: `tests/unit/test_backfill_fetcher.py`

Tests:
1. `test_fetch_writes_one_file_per_symbol` — two symbols → two files
2. `test_fetch_file_is_loadable_by_replay_runner` — output file parses via `ReplayRunner.load_scenario()`
3. `test_fetch_skips_symbol_with_no_bars` — symbol with empty bar list → no file written, no crash
4. `test_fetch_filename_convention` — output is `{SYMBOL}_{days}d.json`
5. `test_fetch_respects_output_dir` — files land in the specified directory

Sweep CLI is thin glue on top of `run_sweep()` which already has coverage via existing sweep logic; no additional unit tests needed for the CLI itself.

---

## Out of Scope

- Incremental / append-mode backfill (always full refetch for simplicity)
- Parquet or CSV formats
- Walk-forward validation splits (separate future task)
- New Postgres tables
- Live-key market data (paper keys cover SIP data for backtesting)
