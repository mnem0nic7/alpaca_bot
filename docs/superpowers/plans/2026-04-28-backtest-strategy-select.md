# Plan: Backtest CLI Strategy Selection

Spec: `docs/superpowers/specs/2026-04-28-backtest-strategy-select.md`

---

## Task 1 — Add `strategy_name` to `BacktestReport`

**File:** `src/alpaca_bot/replay/report.py`

Add `strategy_name: str = "breakout"` field to `BacktestReport`:

```python
@dataclass(frozen=True)
class BacktestReport:
    trades: tuple[ReplayTradeRecord, ...]
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float | None
    mean_return_pct: float | None
    max_drawdown_pct: float | None
    sharpe_ratio: float | None = None
    strategy_name: str = "breakout"
```

Update `build_backtest_report()` to accept and pass through `strategy_name`:

```python
def build_backtest_report(result: ReplayResult, strategy_name: str = "breakout") -> BacktestReport:
    trades = _extract_trades(result.events)
    total = len(trades)
    if total == 0:
        return BacktestReport(
            trades=(),
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate=None,
            mean_return_pct=None,
            max_drawdown_pct=None,
            strategy_name=strategy_name,
        )
    ...
    return BacktestReport(
        ...,
        strategy_name=strategy_name,
    )
```

**Test command:** `pytest tests/unit/ -q -k backtest`

---

## Task 2 — Wire `strategy_name` through `ReplayRunner`

**File:** `src/alpaca_bot/replay/runner.py`

`ReplayRunner.__init__` already stores `signal_evaluator`. Add `strategy_name: str = "breakout"`:

```python
class ReplayRunner:
    def __init__(
        self,
        settings: Settings,
        signal_evaluator: StrategySignalEvaluator | None = None,
        strategy_name: str = "breakout",
    ):
        self.settings = settings
        self.signal_evaluator = signal_evaluator
        self.strategy_name = strategy_name
```

Update `run()` to pass `strategy_name` to `build_backtest_report()`:

```python
result.backtest_report = build_backtest_report(result, strategy_name=self.strategy_name)
```

**Test command:** `pytest tests/unit/ -q -k backtest`

---

## Task 3 — Add `--strategy` flag to CLI

**File:** `src/alpaca_bot/replay/cli.py`

```python
from alpaca_bot.strategy import STRATEGY_REGISTRY

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="alpaca-bot-backtest")
    parser.add_argument("--scenario", required=True, metavar="FILE")
    parser.add_argument("--output", metavar="FILE", default="-")
    parser.add_argument("--format", choices=["json", "csv"], default="json")
    parser.add_argument(
        "--strategy",
        choices=list(STRATEGY_REGISTRY),
        default=None,
        help="Strategy to backtest (default: breakout)",
    )
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    strategy_name = args.strategy or "breakout"
    signal_evaluator = STRATEGY_REGISTRY[args.strategy] if args.strategy else None
    runner = ReplayRunner(settings, signal_evaluator=signal_evaluator, strategy_name=strategy_name)
    scenario = runner.load_scenario(args.scenario)
    result = runner.run(scenario)
    report: BacktestReport = result.backtest_report

    out_text = _format_report(report, args.format)
    if args.output == "-":
        print(out_text)
    else:
        Path(args.output).write_text(out_text)
    return 0
```

Update `_report_to_dict()` to include `"strategy"`:

```python
def _report_to_dict(report: BacktestReport) -> dict:
    return {
        "strategy": report.strategy_name,
        "total_trades": report.total_trades,
        ...
    }
```

Update `_report_to_csv()` to prepend a comment line:

```python
def _report_to_csv(report: BacktestReport) -> str:
    import io
    buf = io.StringIO()
    buf.write(f"# strategy: {report.strategy_name}\n")
    writer = csv.DictWriter(buf, fieldnames=[...])
    ...
```

**Test command:** `pytest tests/unit/ -q -k backtest`

---

## Task 4 — Tests

**File:** `tests/unit/test_backtest_cli.py` (new file if it doesn't exist, or append)

```python
def test_backtest_cli_default_strategy_is_breakout():
    # Run with no --strategy flag; report has strategy_name == "breakout"

def test_backtest_cli_strategy_flag_selects_evaluator():
    # Run with --strategy momentum; runner receives evaluate_momentum_signal

def test_backtest_report_includes_strategy_in_json():
    # JSON output has "strategy": "momentum"

def test_backtest_report_includes_strategy_in_csv():
    # CSV output starts with "# strategy: momentum"

def test_backtest_cli_invalid_strategy_exits():
    # --strategy bogus causes SystemExit (argparse choices validation)
```

Use the existing `ReplayRunner` fake/DI pattern: pass a fake `signal_evaluator` callable that
records its calls and returns `None`, inject via `ReplayRunner(settings, signal_evaluator=fake)`.

**Test command:** `pytest tests/unit/test_backtest_cli.py -v`

---

## Task 5 — Full test suite

```bash
pytest -q
```

All 633+ tests must pass.
