# Plan: Multi-Strategy Validation via Replay/Sweep

Spec: `docs/superpowers/specs/2026-04-30-strategy-validation.md`

---

## Task 1 — Add `--strategy` to `alpaca-bot-sweep`

**File:** `src/alpaca_bot/tuning/sweep_cli.py`

Add `--strategy` argument (default `"breakout"`). Pass `STRATEGY_REGISTRY[args.strategy]` as
`signal_evaluator` to `run_sweep()`.

```python
# Add to argument parser, after --grid:
parser.add_argument(
    "--strategy",
    default="breakout",
    choices=list(STRATEGY_REGISTRY),
    help="Strategy to sweep (default: breakout)",
)
```

Add import at top:
```python
from alpaca_bot.strategy import STRATEGY_REGISTRY
```

Update the `run_sweep()` call inside the for-loop:
```python
candidates = run_sweep(
    scenario=scenario,
    base_env=base_env,
    grid=grid,
    min_trades=args.min_trades,
    signal_evaluator=STRATEGY_REGISTRY[args.strategy],
)
```

Add a test to `tests/unit/test_tuning_sweep_cli.py` (new file):

```python
def test_sweep_cli_strategy_flag_passes_evaluator(monkeypatch, tmp_path):
    """--strategy momentum passes momentum evaluator to run_sweep."""
    import os
    from alpaca_bot.tuning import sweep_cli as module
    from alpaca_bot.strategy import STRATEGY_REGISTRY

    captured = {}

    def fake_run_sweep(scenario, base_env, grid, min_trades, signal_evaluator=None):
        captured["signal_evaluator"] = signal_evaluator
        return []

    monkeypatch.setattr(module, "run_sweep", fake_run_sweep)

    f = tmp_path / "dummy.json"
    f.write_text('{"name":"t","symbol":"X","starting_equity":100000,"daily_bars":[],"intraday_bars":[]}')

    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "false")
    monkeypatch.setenv("STRATEGY_VERSION", "v1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://dummy:dummy@localhost/dummy")
    monkeypatch.setenv("SYMBOLS", "X")
    monkeypatch.setenv("MARKET_DATA_FEED", "sip")
    monkeypatch.setattr(module, "ReplayRunner", FakeReplayRunner)

    import sys
    monkeypatch.setattr(sys, "argv", ["sweep", "--scenario-dir", str(tmp_path), "--strategy", "momentum"])
    try:
        module.main()
    except SystemExit:
        pass
    assert captured.get("signal_evaluator") is STRATEGY_REGISTRY["momentum"]
```

**Test command:** `pytest tests/unit/ -q -k sweep`

---

## Task 2 — Create `scripts/validate_strategies.sh`

**File:** `scripts/validate_strategies.sh` (new, chmod +x)

```bash
#!/usr/bin/env bash
# Runs multi-symbol strategy validation and writes a markdown report.
# Usage: ./scripts/validate_strategies.sh
set -euo pipefail

REPORT="docs/validation-report-$(date +%Y-%m-%d).md"
SCENARIO_DIR="data/backfill"

# Minimal env for replay (no DB connection made, no live trading possible).
# ALPACA_PAPER_API_KEY is optional (str | None) — not needed for replay.
# DATABASE_URL is required by Settings but never used during replay.
export TRADING_MODE=paper
export ENABLE_LIVE_TRADING=false
export STRATEGY_VERSION=v1-validate
export DATABASE_URL="postgresql://dummy:dummy@localhost/dummy"
export SYMBOLS=AAPL
export MARKET_DATA_FEED=sip

echo "# Strategy Validation Report" > "$REPORT"
echo "" >> "$REPORT"
echo "Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$REPORT"
echo "" >> "$REPORT"

echo "## Strategy Comparison (all 252d scenarios)" >> "$REPORT"
echo "" >> "$REPORT"
echo '```' >> "$REPORT"

for f in "$SCENARIO_DIR"/*_252d.json; do
    symbol=$(basename "$f" _252d.json)
    echo "=== $symbol ===" >> "$REPORT"
    alpaca-bot-backtest compare --scenario "$f" --format csv 2>/dev/null >> "$REPORT" || \
        echo "  ERROR: compare failed for $f" >> "$REPORT"
    echo "" >> "$REPORT"
done

echo '```' >> "$REPORT"

echo "## Parameter Sweep — Breakout (all 252d scenarios)" >> "$REPORT"
echo "" >> "$REPORT"
echo '```' >> "$REPORT"
alpaca-bot-sweep --scenario-dir "$SCENARIO_DIR" --strategy breakout 2>/dev/null >> "$REPORT" || \
    echo "  ERROR: sweep failed" >> "$REPORT"
echo '```' >> "$REPORT"

echo "## Parameter Sweep — Momentum (all 252d scenarios)" >> "$REPORT"
echo "" >> "$REPORT"
echo '```' >> "$REPORT"
alpaca-bot-sweep --scenario-dir "$SCENARIO_DIR" --strategy momentum 2>/dev/null >> "$REPORT" || \
    echo "  ERROR: sweep failed" >> "$REPORT"
echo '```' >> "$REPORT"

echo "## Parameter Sweep — ORB (all 252d scenarios)" >> "$REPORT"
echo "" >> "$REPORT"
echo '```' >> "$REPORT"
alpaca-bot-sweep --scenario-dir "$SCENARIO_DIR" --strategy orb 2>/dev/null >> "$REPORT" || \
    echo "  ERROR: sweep failed" >> "$REPORT"
echo '```' >> "$REPORT"

echo "" >> "$REPORT"
echo "Report written to: $REPORT"
```

**Test command:** `bash scripts/validate_strategies.sh && test -f docs/validation-report-*.md`

---

## Task 3 — Run validation and produce report

Execute:
```bash
pip install -e ".[dev]" -q
bash scripts/validate_strategies.sh
```

The script writes `docs/validation-report-2026-04-30.md`.

Review the output for:
- Strategies with `win_rate < 0.45` or `sharpe_ratio < 0.5` across most symbols (candidate for
  disabling via strategy flag)
- Sweep results showing a materially better parameter set vs. current defaults (recommend
  Settings change)

**Test command:** `pytest -q`

---

## Task 4 — Full test suite

```bash
pytest -q
```

All 813+ tests must pass.
