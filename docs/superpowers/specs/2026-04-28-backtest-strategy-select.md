# Spec: Backtest CLI Strategy Selection

## Problem

`alpaca-bot-backtest --scenario FILE` always runs with the default breakout evaluator because
`ReplayRunner` is constructed with no `signal_evaluator` argument. There is no way to replay the
momentum (or any other registered) strategy from the CLI without editing source code.

## Goals

Add a `--strategy` flag to `alpaca-bot-backtest` so operators can test any registered strategy
against a scenario file:

```
alpaca-bot-backtest --scenario scenarios/AAPL.json --strategy momentum
```

The chosen strategy name is included in the JSON report output so results are unambiguous.

## Non-Goals

- Multi-strategy simultaneous replay (one strategy per run; compare by running twice).
- Persistent storage of backtest results (output remains stdout/file as today).
- New scenario file format changes.

## Design

### `--strategy` flag

Optional. Choices: `list(STRATEGY_REGISTRY)`. Default: `None`.

When `None`, behaviour is unchanged — `ReplayRunner(settings)` with no evaluator override,
which defaults to `evaluate_breakout_signal` inside `evaluate_cycle()`.

When set, look up the evaluator: `STRATEGY_REGISTRY[args.strategy]` and pass it to
`ReplayRunner(settings, signal_evaluator=evaluator)`.

### Report includes strategy name

`BacktestReport` gains `strategy_name: str = "breakout"`. CLI passes `args.strategy or "breakout"`.
`_report_to_dict()` includes `"strategy"` in JSON output. CSV output adds a header comment line
`# strategy: <name>` before the data rows (CSV format doesn't support metadata fields cleanly).

## Files Changed

| File | Change |
|------|--------|
| `src/alpaca_bot/replay/cli.py` | Add `--strategy` flag; wire evaluator from registry; pass strategy_name to report |
| `src/alpaca_bot/replay/report.py` | Add `strategy_name: str = "breakout"` to `BacktestReport` |
| `tests/unit/test_backtest_cli.py` | Tests for `--strategy` flag, evaluator injection, report output |

## Safety Analysis

- Replay is entirely offline — no broker calls, no order submission.
- `ENABLE_LIVE_TRADING` gate is irrelevant.
- No DB writes, no audit events.
- No new env vars.
- Invalid `--strategy` value is rejected by argparse `choices=` before any code runs.
