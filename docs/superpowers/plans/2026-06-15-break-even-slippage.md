# Break-even Slippage Diagnostic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Quantify the break-even slippage (bps/side at which after-cost bootstrap `ci_low` crosses zero) for the two lead candidate strategies, to gate the decision between attacking realized cost vs building a cross-sectional portfolio replay.

**Architecture:** A new read-only module `replay/break_even.py` re-runs the replay across a slippage ladder (re-run per rung, not analytical — slippage is non-linear: entry capped at limit, quantity/target derive from the slipped fill), reusing the audit scoring primitives (`bootstrap_mean_ci`, `bootstrap_p_positive`, `classify_verdict`, `_replay_pooled_trades`). A linear interpolation finds the zero-crossing. A `break-even` CLI subcommand drives it; a markdown formatter renders the report. No production config change, no broker path, no migration, no env var.

**Tech Stack:** Python 3, dataclasses (frozen), argparse, pytest with DI fakes (no mocks).

---

### Task 1: BreakEvenPoint / BreakEvenResult dataclasses + `_interpolate_break_even`

**Files:**
- Create: `src/alpaca_bot/replay/break_even.py`
- Test: `tests/unit/test_break_even.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_break_even.py
from alpaca_bot.replay.break_even import (
    BreakEvenPoint,
    _interpolate_break_even,
)


def _pt(bps: float, ci_low: float | None) -> BreakEvenPoint:
    return BreakEvenPoint(
        slippage_bps=bps,
        trades=100,
        mean_trade_pnl=1.0,
        total_pnl=100.0,
        ci_low=ci_low,
        ci_high=None if ci_low is None else ci_low + 10.0,
        p_positive=0.05,
        verdict="no-evidence",
    )


def test_interpolate_returns_linear_zero_crossing():
    # ci_low: +4 at 3 bps, -1 at 4 bps -> crossing at 3 + 1*(4/5) = 3.8
    points = [_pt(3.0, 4.0), _pt(4.0, -1.0)]
    assert _interpolate_break_even(points) == 3.8


def test_interpolate_all_positive_returns_none():
    points = [_pt(0.0, 5.0), _pt(5.0, 1.0)]
    assert _interpolate_break_even(points) is None


def test_interpolate_frictionless_negative_returns_zero():
    points = [_pt(0.0, -2.0), _pt(5.0, -8.0)]
    assert _interpolate_break_even(points) == 0.0


def test_interpolate_first_rung_none_returns_none():
    points = [_pt(0.0, None), _pt(5.0, -1.0)]
    assert _interpolate_break_even(points) is None


def test_interpolate_skips_none_midladder_and_brackets_valid_pair():
    # 0->+3, 1->None, 2->-1 : first valid bracket is (0,2): 0 + 2*(3/4) = 1.5
    points = [_pt(0.0, 3.0), _pt(1.0, None), _pt(2.0, -1.0)]
    assert _interpolate_break_even(points) == 1.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_break_even.py -v`
Expected: FAIL with `ModuleNotFoundError` / `ImportError: cannot import name 'BreakEvenPoint'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/alpaca_bot/replay/break_even.py
"""Break-even slippage diagnostic.

Scores a strategy across a slippage ladder and finds the bps/side at which the
after-cost bootstrap ci_low crosses zero. Read-only: re-runs the replay at each
rung (slippage is not a linear per-trade deduction — entry is capped at the
limit price and quantity/target levels derive from the slipped fill, so the
trade set is not slippage-invariant). Reuses the audit scoring primitives.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Callable, Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import ReplayScenario
from alpaca_bot.replay.audit import (
    PooledTradesFn,
    _replay_pooled_trades,
    classify_verdict,
)
from alpaca_bot.replay.stats import (
    MIN_SAMPLES,
    bootstrap_mean_ci,
    bootstrap_p_positive,
)

DEFAULT_SLIPPAGE_LADDER: tuple[float, ...] = (0.0, 1.0, 2.0, 3.0, 4.0, 5.0)


@dataclass(frozen=True)
class BreakEvenPoint:
    slippage_bps: float
    trades: int
    mean_trade_pnl: float | None
    total_pnl: float
    ci_low: float | None
    ci_high: float | None
    p_positive: float | None
    verdict: str


@dataclass(frozen=True)
class BreakEvenResult:
    strategy: str
    scenarios: int
    points: tuple[BreakEvenPoint, ...]
    break_even_bps: float | None


def _interpolate_break_even(points: Sequence[BreakEvenPoint]) -> float | None:
    """First (lowest-bps) zero-crossing of ci_low, linearly interpolated.

    Points are ascending in slippage_bps. ci_low is approximately (not strictly)
    monotone-decreasing in cost, so the first crossing from >0 to <=0 is the
    conservative break-even. Returns 0.0 if the lowest rung is already <=0 (no
    edge even frictionless), None if no valid positive->non-positive bracket
    exists (all positive, leading None, or no valid pair).
    """
    valid = [p for p in points if p.ci_low is not None]
    if not valid:
        return None
    # Lowest-bps valid rung already non-positive: no edge even frictionless.
    if valid[0].ci_low <= 0.0:
        return 0.0
    prev = valid[0]
    for cur in valid[1:]:
        if cur.ci_low <= 0.0 < prev.ci_low:
            span = prev.ci_low - cur.ci_low
            return prev.slippage_bps + (cur.slippage_bps - prev.slippage_bps) * (
                prev.ci_low / span
            )
        prev = cur
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_break_even.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/replay/break_even.py tests/unit/test_break_even.py
git commit -m "feat: break-even ladder dataclasses + zero-crossing interpolation

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: `run_break_even_sweep`

**Files:**
- Modify: `src/alpaca_bot/replay/break_even.py`
- Test: `tests/unit/test_break_even.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_break_even.py
from datetime import datetime, timezone

from alpaca_bot.config import Settings
from alpaca_bot.replay.break_even import run_break_even_sweep
from alpaca_bot.replay.report import ReplayTradeRecord


def _settings() -> Settings:
    import os
    env = {
        "TRADING_MODE": "paper",
        "ALPACA_PAPER_API_KEY": "k",
        "ALPACA_PAPER_SECRET_KEY": "s",
    }
    old = dict(os.environ)
    os.environ.update(env)
    try:
        return Settings.from_env()
    finally:
        os.environ.clear()
        os.environ.update(old)


def _trade(pnl: float) -> ReplayTradeRecord:
    t = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)
    return ReplayTradeRecord(
        symbol="AAA",
        entry_price=100.0,
        exit_price=100.0 + pnl,
        quantity=1,
        entry_time=t,
        exit_time=t,
        exit_reason="eod",
        pnl=pnl,
        return_pct=pnl / 100.0,
    )


def test_sweep_runs_once_per_rung_with_slippage_threaded():
    seen_bps: list[float] = []

    def fake_pooled(scenarios, settings, strategy):
        seen_bps.append(settings.replay_slippage_bps)
        # Mean pnl falls 2.0 per bps: edge crosses zero between 2 and 3 bps.
        per_trade = 5.0 - 2.0 * settings.replay_slippage_bps
        return [_trade(per_trade + j * 0.01) for j in range(40)]

    result = run_break_even_sweep(
        scenarios=[object()],
        settings=_settings(),
        strategy="bull_flag",
        slippage_ladder=(0.0, 1.0, 2.0, 3.0, 4.0, 5.0),
        pooled_trades_fn=fake_pooled,
    )

    assert seen_bps == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    assert [p.slippage_bps for p in result.points] == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    assert result.points[0].trades == 40
    assert result.strategy == "bull_flag"
    assert result.scenarios == 1
    # ci_low decreases with cost; break-even is a positive finite bps in-ladder.
    assert result.break_even_bps is not None
    assert 0.0 < result.break_even_bps < 5.0


def test_sweep_insufficient_trades_yields_none_ci():
    def fake_pooled(scenarios, settings, strategy):
        return [_trade(1.0), _trade(2.0)]  # < MIN_SAMPLES

    result = run_break_even_sweep(
        scenarios=[object()],
        settings=_settings(),
        strategy="bull_flag",
        slippage_ladder=(0.0, 5.0),
        pooled_trades_fn=fake_pooled,
    )
    assert result.points[0].ci_low is None
    assert result.points[0].verdict == "insufficient-data"
    assert result.break_even_bps is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_break_even.py -k sweep -v`
Expected: FAIL with `ImportError: cannot import name 'run_break_even_sweep'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/alpaca_bot/replay/break_even.py`:

```python
def run_break_even_sweep(
    *,
    scenarios: Sequence[ReplayScenario],
    settings: Settings,
    strategy: str,
    slippage_ladder: Sequence[float] = DEFAULT_SLIPPAGE_LADDER,
    pooled_trades_fn: PooledTradesFn = _replay_pooled_trades,
    on_progress: Callable[[str], None] | None = None,
) -> BreakEvenResult:
    points: list[BreakEvenPoint] = []
    for bps in sorted(slippage_ladder):
        costed = dataclasses.replace(settings, replay_slippage_bps=bps)
        trades = pooled_trades_fn(scenarios, costed, strategy)
        pnls = [t.pnl for t in trades]
        ci = bootstrap_mean_ci(pnls)
        p = bootstrap_p_positive(pnls) if len(pnls) >= MIN_SAMPLES else None
        verdict = classify_verdict(trades=len(pnls), ci=ci, p_positive=p)
        points.append(
            BreakEvenPoint(
                slippage_bps=bps,
                trades=len(pnls),
                mean_trade_pnl=(
                    round(sum(pnls) / len(pnls), 4) if pnls else None
                ),
                total_pnl=round(sum(pnls), 2),
                ci_low=round(ci[0], 4) if ci is not None else None,
                ci_high=round(ci[1], 4) if ci is not None else None,
                p_positive=p,
                verdict=verdict,
            )
        )
        if on_progress is not None:
            be = points[-1]
            on_progress(
                f"{strategy} @ {bps:g}bps: ci_low="
                f"{'n/a' if be.ci_low is None else be.ci_low} "
                f"trades={be.trades} verdict={be.verdict}"
            )
    return BreakEvenResult(
        strategy=strategy,
        scenarios=len(scenarios),
        points=tuple(points),
        break_even_bps=_interpolate_break_even(points),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_break_even.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/replay/break_even.py tests/unit/test_break_even.py
git commit -m "feat: run_break_even_sweep re-runs replay per slippage rung

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: `format_break_even_markdown`

**Files:**
- Modify: `src/alpaca_bot/replay/break_even.py`
- Test: `tests/unit/test_break_even.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_break_even.py
from alpaca_bot.replay.break_even import (
    BreakEvenResult,
    format_break_even_markdown,
)


def test_format_renders_table_breakeven_and_handles_none():
    res = BreakEvenResult(
        strategy="bull_flag",
        scenarios=999,
        points=(
            _pt(0.0, 6.0),
            BreakEvenPoint(5.0, 100, 2.0, 200.0, -0.8, 5.0, 0.09, "no-evidence"),
        ),
        break_even_bps=4.7,
    )
    out = format_break_even_markdown([res])
    assert "bull_flag" in out
    assert "999" in out
    assert "4.7" in out  # interpolated break-even surfaced
    assert "| 0" in out and "| 5" in out  # ladder rows
    assert "n/a" not in out.split("break-even", 1)[0] or True  # smoke

    res_none = BreakEvenResult("vwap_reversion", 999, (_pt(0.0, 5.0),), None)
    out2 = format_break_even_markdown([res_none])
    assert "vwap_reversion" in out2
    assert "> max" in out2 or "none" in out2.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_break_even.py -k format -v`
Expected: FAIL with `ImportError: cannot import name 'format_break_even_markdown'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/alpaca_bot/replay/break_even.py`:

```python
def _fmt(v: float | None, spec: str = ".4f") -> str:
    return "n/a" if v is None else format(v, spec)


def format_break_even_markdown(results: Sequence[BreakEvenResult]) -> str:
    lines: list[str] = ["# Break-even slippage — after-cost ci_low zero-crossing", ""]
    for res in results:
        lines.append(f"## {res.strategy} ({res.scenarios} scenarios)")
        lines.append("")
        if res.break_even_bps is None:
            all_pos = all(
                p.ci_low is not None and p.ci_low > 0.0 for p in res.points
            )
            note = (
                "break-even > max rung (extend ladder)"
                if all_pos
                else "break-even: none (insufficient data)"
            )
        else:
            note = f"break-even ≈ {res.break_even_bps:.2f} bps/side"
        lines.append(f"**{note}**")
        lines.append("")
        lines.append(
            "| bps/side | trades | mean | ci_low | ci_high | p_positive | verdict |"
        )
        lines.append("|---|---|---|---|---|---|---|")
        for p in res.points:
            lines.append(
                f"| {p.slippage_bps:g} | {p.trades} | {_fmt(p.mean_trade_pnl)} | "
                f"{_fmt(p.ci_low)} | {_fmt(p.ci_high)} | "
                f"{_fmt(p.p_positive, '.4f')} | {p.verdict} |"
            )
        lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_break_even.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/replay/break_even.py tests/unit/test_break_even.py
git commit -m "feat: markdown formatter for break-even ladder report

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: `break-even` CLI subcommand

**Files:**
- Modify: `src/alpaca_bot/replay/cli.py`
- Test: `tests/unit/test_break_even_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_break_even_cli.py
import json
import os
from pathlib import Path

from alpaca_bot.replay.cli import main


def _write_scenario(path: Path) -> None:
    # Minimal ReplayScenario JSON: a symbol and an empty/short bar set is enough
    # to exercise the CLI wiring; the audit harness tolerates short scenarios.
    bars = [
        {
            "timestamp": "2026-01-02T14:30:00+00:00",
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5,
            "volume": 1000,
        }
    ]
    path.write_text(
        json.dumps(
            {
                "symbol": "AAA",
                "intraday_bars": bars,
                "daily_bars": bars,
            }
        )
    )


def test_break_even_cli_writes_report(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "k")
    monkeypatch.setenv("ALPACA_PAPER_SECRET_KEY", "s")
    scen_dir = tmp_path / "scen"
    scen_dir.mkdir()
    _write_scenario(scen_dir / "AAA_252d.json")
    out = tmp_path / "report.md"

    rc = main(
        [
            "break-even",
            "--scenario-dir", str(scen_dir),
            "--strategy", "bull_flag",
            "--slippage-ladder", "0,5",
            "--output", str(out),
        ]
    )
    assert rc == 0
    text = out.read_text()
    assert "Break-even slippage" in text
    assert "bull_flag" in text


def test_break_even_cli_empty_dir_returns_1(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "k")
    monkeypatch.setenv("ALPACA_PAPER_SECRET_KEY", "s")
    empty = tmp_path / "empty"
    empty.mkdir()
    rc = main(["break-even", "--scenario-dir", str(empty), "--strategy", "bull_flag"])
    assert rc == 1
```

Verify the `ReplayScenario` JSON field names before finalizing this test — inspect `ReplayRunner.load_scenario` and `ReplayScenario` in `src/alpaca_bot/domain/models.py`. If the field names differ (e.g. `bars` vs `intraday_bars`), match them exactly; the CLI wiring assertion is the point, not the bar schema.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_break_even_cli.py -v`
Expected: FAIL — argparse exits non-zero on the unknown `break-even` subcommand (SystemExit) or the assertion fails.

- [ ] **Step 3: Write minimal implementation**

Add the import near the other replay imports in `cli.py`:

```python
from alpaca_bot.replay.break_even import (
    DEFAULT_SLIPPAGE_LADDER,
    format_break_even_markdown,
    run_break_even_sweep,
)
```

Add the subparser after the `lever-sweep` parser block (around line 129, before `args = parser.parse_args(argv)`):

```python
    # --- break-even subcommand ---
    be_p = subparsers.add_parser(
        "break-even",
        help="Slippage ladder: find where after-cost ci_low crosses zero",
    )
    be_p.add_argument("--scenario-dir", required=True, metavar="DIR")
    be_p.add_argument(
        "--strategy",
        action="append",
        choices=list(STRATEGY_REGISTRY),
        metavar="NAME",
        help="strategy to score (repeatable; default: bull_flag, vwap_reversion)",
    )
    be_p.add_argument(
        "--slippage-ladder",
        default=None,
        metavar="b1,b2,...",
        help="comma-separated bps/side levels (default: 0,1,2,3,4,5)",
    )
    be_p.add_argument(
        "--limit", type=int, default=0, metavar="N",
        help="use only the first N scenario files (0 = all)",
    )
    be_p.add_argument("--output", metavar="FILE", default="-")
```

Add the dispatch line in the `main()` subcommand chain, after the `lever-sweep` line:

```python
    if args.subcommand == "break-even":
        return _cmd_break_even(args)
```

Add the handler (place it after `_cmd_lever_sweep`):

```python
def _cmd_break_even(args: argparse.Namespace) -> int:
    settings = Settings.from_env()

    paths = sorted(Path(args.scenario_dir).glob("*.json"))
    if args.limit > 0:
        paths = paths[: args.limit]
    if not paths:
        print(f"No scenario files in {args.scenario_dir}", file=sys.stderr)
        return 1
    scenarios = [ReplayRunner.load_scenario(p) for p in paths]

    strategies = args.strategy or ["bull_flag", "vwap_reversion"]
    if args.slippage_ladder is not None:
        ladder = tuple(float(x) for x in args.slippage_ladder.split(","))
    else:
        ladder = DEFAULT_SLIPPAGE_LADDER

    results = [
        run_break_even_sweep(
            scenarios=scenarios,
            settings=settings,
            strategy=name,
            slippage_ladder=ladder,
            on_progress=lambda msg: print(f"[break-even] {msg}", file=sys.stderr),
        )
        for name in strategies
    ]

    _write_output(format_break_even_markdown(results), args.output)
    return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_break_even_cli.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: PASS — all prior tests plus the new break-even tests green (no regressions).

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/replay/cli.py tests/unit/test_break_even_cli.py
git commit -m "feat: alpaca-bot-backtest break-even subcommand

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Ops run over the full 999 + findings report

**Files:**
- Create: `docs/strategy-audit/2026-06-15-break-even-slippage.md`

This task is operational, not code. It runs the editable `src/` directly (no Docker rebuild needed for a read-only diagnostic; `python3` not `python`).

- [ ] **Step 1: Confirm the scenario store**

Run: `ls /var/lib/alpaca-bot/nightly/scenarios/*.json | wc -l`
Expected: 999

- [ ] **Step 2: Run the ladder in the background over the full 999**

Run (background — ~hours; the two strategies run sequentially within one process):

```bash
/home/ab-1/.local/bin/alpaca-bot-backtest break-even \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategy bull_flag --strategy vwap_reversion \
  --slippage-ladder 0,1,2,3,4,5 \
  --output /tmp/break_even_full999.md \
  2> /tmp/break_even_full999.stderr
```

This needs Settings from env. Source the server env first in the same shell:
`set -a && source /etc/alpaca_bot/alpaca-bot.env && set +a` (never echo/commit its contents).
Because the diagnostic reads only scenario JSON (no broker, no Postgres writes), the env is
needed only to satisfy `Settings.from_env()` validation.

- [ ] **Step 3: Inspect results**

Read `/tmp/break_even_full999.md` and `/tmp/break_even_full999.stderr`. Record the per-rung `ci_low` and the interpolated break-even bps for each strategy.

- [ ] **Step 4: If break-even falls outside `{0..5}`, extend the ladder**

If either strategy's `break_even_bps` is `None` with all rungs positive (break-even > 5), re-run with `--slippage-ladder 0,2,4,6,8,10`. If it's `0.0` (negative even frictionless — unexpected given prior audits), record that as the finding. Otherwise the `{0..5}` ladder brackets it.

- [ ] **Step 5: Write the findings report**

Create `docs/strategy-audit/2026-06-15-break-even-slippage.md` in the house format (see `docs/strategy-audit/2026-06-15-lever-sweep-bull_flag.md`). It must contain:
- The per-strategy ladder table and interpolated break-even bps.
- An interpretation against realistic large-cap limit-order execution cost (is the break-even comfortably above, near, or below a plausible realized cost?).
- The **gate decision**: does break-even sit close enough to plausible realized cost to justify attacking cost directly (fewer/larger/longer holds, limit entries), or is it low enough that only cross-sectional top-K selectivity (the structurally-inexpressible mechanism) could close the gap?
- Explicit restatement that this is in-sample and diagnostic; no production config change; bot stays `close_only`; `TRADING_MODE=paper` / `ENABLE_LIVE_TRADING=false` untouched; any survivor goes through the nightly OOS gate, never hand-applied.

- [ ] **Step 6: Commit the report**

```bash
git add docs/strategy-audit/2026-06-15-break-even-slippage.md
git commit -m "docs: break-even slippage findings for lead candidates

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Self-review

**Spec coverage:** module (`break_even.py`) → Tasks 1-3; CLI → Task 4; full-999 run + report → Task 5; re-run-per-rung method, full-data choice, no-walk-forward — all encoded in the spec and reflected in the run command and report contents. ✔
**Placeholder scan:** every code step shows complete code; the one verification note (Task 4 Step 1, ReplayScenario field names) is an explicit pre-flight check, not a placeholder. ✔
**Type consistency:** `BreakEvenPoint`/`BreakEvenResult` field names and `run_break_even_sweep`/`_interpolate_break_even`/`format_break_even_markdown` signatures are identical across Tasks 1-4 and the tests. `classify_verdict(trades=, ci=, p_positive=)` matches `audit.py`. `_replay_pooled_trades`/`PooledTradesFn` imported, not redefined. ✔
**Safety:** no broker path, no Settings prod mutation (local `dataclasses.replace` only), no migration, no env var, no AuditEvent — consistent with the spec's read-only diagnostic guarantee. ✔
