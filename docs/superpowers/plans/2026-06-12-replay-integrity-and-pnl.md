# Replay Integrity, Honest Re-evaluation, and P&L Response — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the two replay-harness defects (symbol-universe mismatch, look-ahead daily trend gate), re-run the cost-aware strategy audit over the genuinely-999 nightly scenarios, apply an evidence-gated config response, and run a full code+security review of the trading-critical paths.

**Architecture:** Runner-local fix (Approach A of the spec): `ReplayRunner.run()` passes `symbols=(scenario.symbol,)` to the pure engine and slices the daily series point-in-time per session day, mirroring the live supervisor's data shape (daily series ends at the prior completed day). All 23 evaluators, the sweep, the audit, and the nightly pipeline inherit the fix for free. No engine, evaluator, or live-path changes.

**Tech Stack:** Python 3 / pytest (DI fakes, no mocks), Docker Compose ops on the prod server, parallel review subagents.

**Spec:** `docs/superpowers/specs/2026-06-12-replay-integrity-and-pnl-design.md`
**Baseline:** 1,971 passing tests. `python` is not on PATH — use `python3`.
**Commit trailer (every commit):** `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

**Verified facts this plan relies on (do not re-derive):**
- `evaluate_cycle()` signature: `symbols: tuple[str, ...] | None = None` (src/alpaca_bot/core/engine.py:109); iterates `for symbol in (symbols or settings.symbols)` (engine.py:686).
- Engine stale-daily guard (engine.py:774-778): entry rejected if `(now - daily_bars[-1].timestamp).days > viability_daily_bar_max_age_days` (default 5). After point-in-time slicing the last daily bar is 1–3 days old → passes.
- `daily_trend_filter_passes` (src/alpaca_bot/strategy/breakout.py:28-36): needs `len >= daily_sma_period + 1` (21 with the default 20); window is the **last 21 bars excluding the final one** — so prepending an earlier bar to a series is a no-op under the current (pre-fix) code.
- `calculate_atr` (src/alpaca_bot/risk/atr.py): Wilder smoothing over the full passed series. For a series with **constant true range** (all golden/test fixtures are uniform ramps, TR = 1.3 or 2.0), ATR is invariant to adding/removing pattern-consistent bars — so fixture prep and the slice both leave ATR unchanged for those fixtures.
- Golden fixtures (`tests/golden/breakout_success.json`, `breakout_entry_expires.json`): 21 daily bars 2026-04-04→2026-04-24 (uniform +1/day ramp, range 1.3), all intraday bars on 2026-04-24. After slicing, only 20 bars precede the intraday day → trend filter would fail → **fixtures must gain one earlier bar first (Task 2)**.
- `tests/unit/test_tuning_sweep.py` quiet-scenario daily bars (line 58) end 2026-04-23 — already strictly before the intraday day with 25 bars; no change needed.
- Compose `nightly` service mounts `/var/lib/alpaca-bot/nightly:/data` (deploy/compose.yaml:162) and is in the `ops` profile; `docker compose run --rm nightly <cmd>` overrides its command.
- Audit CLI: `alpaca-bot-backtest audit --scenario-dir DIR --slippage-bps BPS --output FILE --json FILE [--strategies s1,s2] [--limit N]`.
- Live strategy is selected by `STRATEGY_VERSION=v1-breakout` in `/etc/alpaca_bot/alpaca-bot.env`; `TRADING_MODE=paper`, `ENABLE_LIVE_TRADING=false` — both untouched by this plan.

---

### Task 1: Defect-1 regression — scenario symbol must reach the engine

**Files:**
- Create: `tests/unit/test_replay_point_in_time.py`
- Modify: `src/alpaca_bot/replay/runner.py` (the `evaluate_cycle(...)` call, currently lines 110-121)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_replay_point_in_time.py`:

```python
"""Regression tests for the two replay-harness defects found in the
2026-06-11 contrarian audit (docs/strategy-audit/):

1. ReplayRunner never passed the scenario symbol to evaluate_cycle(), so
   scenarios for symbols outside settings.symbols were silently never
   evaluated (991/999 nightly scenarios produced zero decisions).
2. ReplayRunner passed the FULL daily series on every intraday bar, so
   end-anchored daily trend filters were look-ahead and scenario-constant.

The fixed runner must mirror the live supervisor's data shape: on session
day D the engine sees only daily bars dated < D (runtime/supervisor.py
fetches daily bars with end = midnight ET of the session date).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from alpaca_bot.config import Settings
from alpaca_bot.domain import Bar
from alpaca_bot.domain.enums import IntentType
from alpaca_bot.domain.models import EntrySignal, ReplayScenario
from alpaca_bot.replay import ReplayRunner
from alpaca_bot.strategy.breakout import session_day


def make_settings(**overrides: str) -> Settings:
    values = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1-breakout",
        "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "AAPL,MSFT,SPY",
        "DAILY_SMA_PERIOD": "20",
        "BREAKOUT_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_THRESHOLD": "1.5",
        "ENTRY_TIMEFRAME_MINUTES": "15",
        "RISK_PER_TRADE_PCT": "0.0025",
        "MAX_POSITION_PCT": "0.05",
        "MAX_OPEN_POSITIONS": "3",
        "DAILY_LOSS_LIMIT_PCT": "0.01",
        "STOP_LIMIT_BUFFER_PCT": "0.001",
        "BREAKOUT_STOP_BUFFER_PCT": "0.001",
        "ENTRY_STOP_PRICE_BUFFER": "0.01",
        "ENTRY_WINDOW_START": "10:00",
        "ENTRY_WINDOW_END": "15:30",
        "FLATTEN_TIME": "15:45",
        "REPLAY_SLIPPAGE_BPS": "0",
        "ATR_PERIOD": "14",
    }
    values.update(overrides)
    return Settings.from_env(values)


def _daily_bars(symbol: str, *, start: datetime, count: int) -> list[Bar]:
    """Uniform +1/day ramp with constant true range (2.0), so ATR and the
    trend-filter verdict are invariant to where the series is cut."""
    return [
        Bar(
            symbol=symbol,
            timestamp=start + timedelta(days=i),
            open=88.0 + i,
            high=89.0 + i,
            low=87.0 + i,
            close=89.0 + i,
            volume=1_000_000,
        )
        for i in range(count)
    ]


def _fires_at_index_5(*, symbol, intraday_bars, signal_index, daily_bars, settings):
    if signal_index != 5:
        return None
    return EntrySignal(
        symbol=symbol,
        signal_bar=intraday_bars[signal_index],
        entry_level=100.5,
        relative_volume=2.0,
        stop_price=101.0,
        limit_price=101.5,
        initial_stop_price=99.0,
    )


def test_off_watchlist_scenario_symbol_is_evaluated() -> None:
    """Defect 1: NVDA is not in SYMBOLS, but its scenario must still be evaluated."""
    settings = make_settings()  # SYMBOLS=AAPL,MSFT,SPY — no NVDA
    daily = _daily_bars(
        "NVDA", start=datetime(2026, 4, 3, 20, 0, tzinfo=timezone.utc), count=21
    )  # ends 2026-04-23, strictly before the intraday day
    t0 = datetime(2026, 4, 24, 14, 30, tzinfo=timezone.utc)  # 10:30 ET
    intraday = [
        Bar(
            symbol="NVDA",
            timestamp=t0 + timedelta(minutes=15 * i),
            open=100.0,
            high=100.5,
            low=99.5,
            close=100.0,
            volume=1_000_000,
        )
        for i in range(10)
    ]
    scenario = ReplayScenario(
        name="off-watchlist",
        symbol="NVDA",
        starting_equity=100_000.0,
        daily_bars=daily,
        intraday_bars=intraday,
    )

    result = ReplayRunner(settings, signal_evaluator=_fires_at_index_5).run(scenario)

    placed = [e for e in result.events if e.event_type == IntentType.ENTRY_ORDER_PLACED]
    assert placed, "scenario symbol outside settings.symbols was never evaluated"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/unit/test_replay_point_in_time.py::test_off_watchlist_scenario_symbol_is_evaluated -v`
Expected: FAIL — `placed` is empty (the engine iterated AAPL/MSFT/SPY, which have no bars).

- [ ] **Step 3: Implement — pass the scenario symbol to the engine**

In `src/alpaca_bot/replay/runner.py`, add one argument to the `evaluate_cycle` call:

```python
            cycle_result = evaluate_cycle(
                settings=self.settings,
                now=bar.timestamp,
                equity=state.equity,
                intraday_bars_by_symbol=intraday_by_symbol,
                daily_bars_by_symbol=daily_by_symbol,
                open_positions=open_positions,
                working_order_symbols=working_order_symbols,
                traded_symbols_today=state.traded_symbols,
                entries_disabled=False,
                signal_evaluator=self.signal_evaluator,
                symbols=(scenario.symbol,),
            )
```

- [ ] **Step 4: Run the test to verify it passes, then the full suite**

Run: `pytest tests/unit/test_replay_point_in_time.py -v` → PASS
Run: `pytest` → expected 1,972 passed. (Existing replay tests all use scenario symbols inside the default watchlist; narrowing the engine's iteration set to the one symbol that has data is behavior-equivalent for them.)

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_replay_point_in_time.py src/alpaca_bot/replay/runner.py
git commit -m "fix: replay runner passes scenario symbol to evaluate_cycle

Off-watchlist scenarios (991 of 999 nightly files) were silently never
evaluated because the engine iterated settings.symbols.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Fixture prep — one extra earlier daily bar (exact no-op today)

The point-in-time slice (Task 3) drops the intraday-day bar from every daily series. Fixtures with exactly 21 daily bars ending ON the intraday day would drop to 20 < `period+1` and fail the trend filter. Prepending one pattern-consistent earlier bar is an exact no-op under the current code (trend window = last 21 excluding final; ATR invariant for constant-TR series), so this lands green before the behavior change.

**Files:**
- Modify: `tests/golden/breakout_success.json` (daily_bars)
- Modify: `tests/golden/breakout_entry_expires.json` (daily_bars)
- Modify: `tests/unit/test_replay_runner_engine_delegation.py` (`_make_daily_bars`)

- [ ] **Step 1: Prepend a 2026-04-03 daily bar to both golden fixtures**

In each JSON file, insert this object as the FIRST element of the `daily_bars` array (the existing first element is the 2026-04-04 bar with open 88.5):

```json
{"symbol": "AAPL", "timestamp": "2026-04-03T20:00:00+00:00", "open": 87.5, "high": 88.3, "low": 87.0, "close": 88.0, "volume": 1000000}
```

This continues the ramp backwards exactly (next bar's TR vs this close: max(1.3, |89.3−88.0|, |88.0−88.0|) = 1.3, identical to every other TR).

- [ ] **Step 2: Replace `_make_daily_bars` in `tests/unit/test_replay_runner_engine_delegation.py`**

Old helper: 21 bars starting `2026-04-04`, `open=89.0+i / high=90.0+i / low=88.0+i / close=90.0+i / volume=1_000_000+i*1000`. New helper produces the identical bars plus one earlier bar (per-date values unchanged: at index shift i→i+1 the formulas below reproduce the old values exactly):

```python
def _make_daily_bars(symbol: str = "AAPL") -> list[Bar]:
    # 22 bars ending 2026-04-24 (same day as the intraday bars). The runner's
    # point-in-time slice drops the 2026-04-24 bar, leaving 21 completed days —
    # enough for daily_trend_filter_passes with sma_period=20 (needs period+1
    # bars) — with a last-bar age of 1 day (< viability max of 5).
    start = datetime(2026, 4, 3, 20, 0, tzinfo=timezone.utc)
    return [
        Bar(
            symbol=symbol,
            timestamp=start + timedelta(days=i),
            open=88.0 + i,
            high=89.0 + i,
            low=87.0 + i,
            close=89.0 + i,
            volume=999_000 + i * 1000,
        )
        for i in range(22)
    ]
```

- [ ] **Step 3: Run the full suite — must be green (proves the no-op)**

Run: `pytest`
Expected: 1,972 passed, zero failures. If anything fails here, the prep was NOT a no-op — stop and diagnose before proceeding (do not re-pin anything in this task).

- [ ] **Step 4: Commit**

```bash
git add tests/golden/breakout_success.json tests/golden/breakout_entry_expires.json tests/unit/test_replay_runner_engine_delegation.py
git commit -m "test: extend daily-bar fixtures one day earlier ahead of point-in-time slicing

No-op under current code (trend window is last-21-excluding-final; constant-TR
series keeps ATR unchanged). Gives every fixture 21 completed days strictly
before its intraday day.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Defect-2 regression — point-in-time daily slicing

**Files:**
- Modify: `tests/unit/test_replay_point_in_time.py` (add two tests)
- Modify: `src/alpaca_bot/replay/runner.py` (`run()`)

- [ ] **Step 1: Write the two failing tests**

Append to `tests/unit/test_replay_point_in_time.py`:

```python
def test_daily_slice_is_point_in_time() -> None:
    """Defect 2: on session day D the evaluator must never see a daily bar
    dated >= D, and the slice must grow as the scenario crosses days."""
    settings = make_settings()
    captured: list[tuple[datetime, tuple[Bar, ...]]] = []

    def capturing_evaluator(*, symbol, intraday_bars, signal_index, daily_bars, settings):
        captured.append((intraday_bars[signal_index].timestamp, tuple(daily_bars)))
        return None

    # 22 daily bars 2026-04-03..2026-04-24 — includes bars dated ON both
    # intraday days, which the slice must hide.
    daily = _daily_bars(
        "AAPL", start=datetime(2026, 4, 3, 20, 0, tzinfo=timezone.utc), count=22
    )
    intraday: list[Bar] = []
    for day_num in (23, 24):
        t0 = datetime(2026, 4, day_num, 14, 30, tzinfo=timezone.utc)  # 10:30 ET
        intraday.extend(
            Bar(
                symbol="AAPL",
                timestamp=t0 + timedelta(minutes=15 * i),
                open=100.0,
                high=100.5,
                low=99.5,
                close=100.0,
                volume=1_000_000,
            )
            for i in range(8)
        )
    scenario = ReplayScenario(
        name="two-day",
        symbol="AAPL",
        starting_equity=100_000.0,
        daily_bars=daily,
        intraday_bars=intraday,
    )

    ReplayRunner(settings, signal_evaluator=capturing_evaluator).run(scenario)

    assert captured, "evaluator was never invoked"
    tz = settings.market_timezone
    sizes: dict[date, int] = {}
    for signal_ts, daily_slice in captured:
        day = session_day(signal_ts, settings)
        assert daily_slice, "daily slice was empty"
        assert max(b.timestamp.astimezone(tz).date() for b in daily_slice) < day
        sizes[day] = len(daily_slice)
    assert sorted(sizes) == [date(2026, 4, 23), date(2026, 4, 24)]
    # Exactly one more completed day visible on the second session day.
    assert sizes[date(2026, 4, 24)] == sizes[date(2026, 4, 23)] + 1


def _breakout_day(symbol: str, day_start_utc: datetime) -> list[Bar]:
    """One session: 20 quiet bars from 10:00 ET, a high-volume breakout bar at
    15:00 ET, an execution bar, and bars out to the 15:45 ET flatten."""
    t0 = day_start_utc.replace(hour=14, minute=0)  # 10:00 ET
    bars = [
        Bar(
            symbol=symbol,
            timestamp=t0 + timedelta(minutes=15 * i),
            open=100.0,
            high=100.5,
            low=99.5,
            close=100.0,
            volume=1_000_000,
        )
        for i in range(20)
    ]
    breakout_ts = t0 + timedelta(minutes=15 * 20)  # 15:00 ET, inside entry window
    bars.append(
        Bar(symbol=symbol, timestamp=breakout_ts,
            open=100.4, high=102.0, low=100.3, close=101.8, volume=2_500_000)
    )
    bars.append(  # execution bar: opens above stop 100.51, below limit 100.61
        Bar(symbol=symbol, timestamp=breakout_ts + timedelta(minutes=15),
            open=100.55, high=101.2, low=100.4, close=100.9, volume=1_200_000)
    )
    bars.append(
        Bar(symbol=symbol, timestamp=breakout_ts + timedelta(minutes=30),
            open=100.9, high=101.0, low=100.5, close=100.8, volume=900_000)
    )
    bars.append(  # 15:45 ET — engine emits the EOD flatten exit here
        Bar(symbol=symbol, timestamp=breakout_ts + timedelta(minutes=45),
            open=100.8, high=100.9, low=100.4, close=100.6, volume=900_000)
    )
    return bars


def test_trend_gate_varies_within_scenario() -> None:
    """Defect 2 end-to-end with the real breakout evaluator: an uptrend that
    breaks mid-scenario must allow entries while intact and block them after.

    Old behavior: the full-series trend filter saw the post-crash close on
    every bar, so the entire scenario produced zero entries."""
    settings = make_settings()
    symbol = "AAPL"
    rising = [  # 2026-03-28 .. 2026-04-21, close 100 -> 124
        Bar(
            symbol=symbol,
            timestamp=datetime(2026, 3, 28, 20, 0, tzinfo=timezone.utc) + timedelta(days=i),
            open=99.5 + i,
            high=100.5 + i,
            low=99.0 + i,
            close=100.0 + i,
            volume=1_000_000,
        )
        for i in range(25)
    ]
    crash = Bar(
        symbol=symbol,
        timestamp=datetime(2026, 4, 22, 20, 0, tzinfo=timezone.utc),
        open=123.5, high=124.0, low=79.5, close=80.0, volume=5_000_000,
    )
    flat = [
        Bar(
            symbol=symbol,
            timestamp=datetime(2026, 4, d, 20, 0, tzinfo=timezone.utc),
            open=80.0, high=80.5, low=79.5, close=80.0, volume=1_000_000,
        )
        for d in (23, 24)
    ]
    daily = rising + [crash] + flat

    intraday = _breakout_day(symbol, datetime(2026, 4, 23, tzinfo=timezone.utc)) + _breakout_day(
        symbol, datetime(2026, 4, 24, tzinfo=timezone.utc)
    )
    scenario = ReplayScenario(
        name="trend-breaks",
        symbol=symbol,
        starting_equity=100_000.0,
        daily_bars=daily,
        intraday_bars=intraday,
    )

    result = ReplayRunner(settings).run(scenario)

    placed = [e for e in result.events if e.event_type == IntentType.ENTRY_ORDER_PLACED]
    assert len(placed) == 1, f"expected exactly one entry (day 1 only), got {len(placed)}"
    assert session_day(placed[0].timestamp, settings) == date(2026, 4, 23)
```

Why the trend gate flips between the two days (with `daily_sma_period=20`; `daily_trend_filter_passes` uses window = last 21 bars excluding the final, comparing `window[-1].close` to the window SMA):
- Day 1 (04-23) slice = 25 rising + crash (26 bars). Window excludes the crash; `window[-1]` = 04-21 close 124 > SMA ≈ 114.5 → **passes**.
- Day 2 (04-24) slice = + flat 04-23 (27 bars). `window[-1]` = crash close 80 < SMA ≈ 113 → **fails**.
- Old full-series behavior: `window[-1]` = flat 04-23 close 80 → fails on **both** days → zero entries → the `len == 1` assertion fails pre-fix.

- [ ] **Step 2: Run both tests to verify they fail**

Run: `pytest tests/unit/test_replay_point_in_time.py -v`
Expected: `test_off_watchlist_scenario_symbol_is_evaluated` PASS (Task 1); the two new tests FAIL (slice contains same-day bars; zero entries placed).

- [ ] **Step 3: Implement point-in-time slicing in `ReplayRunner.run()`**

In `src/alpaca_bot/replay/runner.py`, change the top of `run()` and the per-bar dict construction:

```python
    def run(self, scenario: ReplayScenario) -> ReplayResult:
        bars = sorted(scenario.intraday_bars, key=lambda bar: bar.timestamp)
        sorted_daily = sorted(scenario.daily_bars, key=lambda bar: bar.timestamp)
        state = ReplayState(equity=scenario.starting_equity)
        events: list[ReplayEvent] = []
        current_day: date | None = None
        daily_slice: list[Bar] = []
```

and replace the two lines

```python
            bars_slice = bars[: index + 1]
            intraday_by_symbol = {bar.symbol: bars_slice}
            daily_by_symbol = {bar.symbol: scenario.daily_bars}
```

with

```python
            bars_slice = bars[: index + 1]
            intraday_by_symbol = {bar.symbol: bars_slice}
            # Mirror live data shape: the supervisor fetches daily bars with
            # end = midnight ET of the session date, so the series the engine
            # sees on day D contains only bars from completed days (< D).
            day = session_day(bar.timestamp, self.settings)
            if day != current_day:
                current_day = day
                daily_slice = [
                    b
                    for b in sorted_daily
                    if b.timestamp.astimezone(self.settings.market_timezone).date() < day
                ]
            daily_by_symbol = {bar.symbol: daily_slice}
```

(`session_day` and `date` are already imported in this file. The slice is recomputed only when the session day changes — once per day, not per bar.)

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `pytest tests/unit/test_replay_point_in_time.py -v`
Expected: 3 passed.

- [ ] **Step 5: Run the full suite and triage**

Run: `pytest`
Expected: green, because Task 2 made every runner-mediated fixture slice-safe and the constant-TR ramps keep ATR/trend verdicts identical. If any test fails, apply the triage rule below — likely suspects are `tests/unit/test_replay_golden.py`, `test_replay_slippage.py`, `test_replay_report.py`, `test_backtest_cli.py`, `test_tuning_sweep.py`, `test_nightly_cli.py`, `test_replay_runner_engine_delegation.py`.

**Triage rule:** a failing expectation may be re-pinned ONLY after confirming by hand that the new value is what a point-in-time daily series (bars < session day) produces. Recompute the trend-filter window and ATR for the sliced series before touching the assertion; record the reasoning in the commit message. If a failure shows the slice itself is wrong (e.g. a bar dated ≥ session day leaking through), fix the runner, not the test.

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_replay_point_in_time.py src/alpaca_bot/replay/runner.py
git commit -m "fix: replay daily bars are sliced point-in-time per session day

The runner passed the full daily series on every intraday bar, making
end-anchored trend filters look-ahead and scenario-constant. Now the
engine sees only completed days (< session day), matching the live
supervisor's daily-bar fetch (end = midnight ET of session date).

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Deploy the fixed harness

Phase 2 runs inside the deployed image, so the image must be rebuilt first. This deploy also ships the (replay-only) fix to prod services; it does not change live trading behavior.

- [ ] **Step 1: Pre-deploy gate**

Run: `pytest` → must be green (expect 1,974 passed).

- [ ] **Step 2: Deploy**

```bash
cd /workspace/alpaca_bot && ./scripts/deploy.sh /etc/alpaca_bot/alpaca-bot.env
```

Expected: migrate runs (no new migrations — "up to date"), web and supervisor healthy.

- [ ] **Step 3: Verify health**

```bash
set -a && source /etc/alpaca_bot/alpaca-bot.env && set +a
docker compose -f deploy/compose.yaml run --rm admin alpaca-bot-ops-check
```

(There is no `ops-check` service; `alpaca-bot-ops-check` runs as a command override on the `admin` service, which shares the Docker network with `web`.) Expected: `status=ok db=ok trading_mode=paper trading_status=enabled worker_status=fresh`. Confirm `docker compose -f deploy/compose.yaml ps` shows supervisor and web up.

No commit (no repo changes).

---

### Task 5: Honest re-evaluation — audit + sweeps + report (Phase 2)

Can run concurrently with Task 7 (the review agents); Task 6 depends on this task's output.

- [ ] **Step 1: Launch the audit over the nightly scenario store (background — 999 scenarios × 11 strategies × 2 runs takes a while)**

```bash
cd /workspace/alpaca_bot
set -a && source /etc/alpaca_bot/alpaca-bot.env && set +a
docker compose -f deploy/compose.yaml run --rm nightly \
  alpaca-bot-backtest audit \
  --scenario-dir /data/scenarios \
  --slippage-bps 5 \
  --output /data/audit-2026-06-12.md \
  --json /data/audit-2026-06-12.json
```

Run via Bash `run_in_background: true`. Progress lines (`[audit] <strategy>: N trades, verdict=...`) stream to stderr. Output lands on the host at `/var/lib/alpaca-bot/nightly/audit-2026-06-12.md` / `.json`.

Sanity check on completion: strategies other than momentum should now show **nonzero trades** (the 2026-06-11 run showed 10 of 11 strategies at zero trades — a harness artifact). If every strategy is still at zero trades, the fix did not reach the image — stop and diagnose (`docker compose ... run --rm nightly python3 -c "import inspect; from alpaca_bot.replay.runner import ReplayRunner; print('symbols=' in inspect.getsource(ReplayRunner.run))"`).

- [ ] **Step 2: Re-sweep strategies whose verdict is NOT negative-edge**

For each strategy with verdict `positive-edge`, `no-evidence`, or `insufficient-data` **with trades > 0** in the new audit:

```bash
docker compose -f deploy/compose.yaml run --rm nightly \
  alpaca-bot-sweep --scenario-dir /data/scenarios --strategy <name> \
  > /tmp/sweep-<name>-2026-06-12.txt
```

Run these in background, sequentially or small batches (each is CPU-heavy). Strategies with verdict `negative-edge` and zero-trade strategies are NOT swept (nothing to tune toward / no signal to tune).

- [ ] **Step 3: Write the re-evaluation report**

Create `docs/strategy-audit/2026-06-12-honest-reevaluation.md` with:

```markdown
# Honest re-evaluation — post harness fix (2026-06-12)

Harness defects fixed (commits <task-1-sha>, <task-3-sha>): scenario symbols
now reach the engine; daily series sliced point-in-time per session day.

## Audit — 5 bps/side, 999 scenarios
<verbatim table from /var/lib/alpaca-bot/nightly/audit-2026-06-12.md>

## Delta vs 2026-06-11 audit
| strategy | 06-11 verdict | 06-12 verdict | 06-11 trades | 06-12 trades | note |
... one row per strategy; call out momentum explicitly (old: negative-edge,
313 trades, CI [-6.23, -0.98] — did 991 new symbol-years change it?) ...

## Comparison vs the void R7 nightly results
<which R7 candidate parameters survive / are contradicted>

## Sweep results
<per swept strategy: best parameters, in-sample score, whether they warrant an OOS run>

## Config response decided (see Phase 3)
<the decision table filled in with actual verdicts>
```

Every claim must come from the actual run outputs — no projected numbers.

- [ ] **Step 4: Commit**

```bash
cp /var/lib/alpaca-bot/nightly/audit-2026-06-12.md docs/strategy-audit/2026-06-12-audit-table.md
git add docs/strategy-audit/2026-06-12-honest-reevaluation.md docs/strategy-audit/2026-06-12-audit-table.md
git commit -m "docs: honest strategy re-evaluation over fixed replay harness

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Evidence-gated config response (Phase 3)

Applies the spec's decision rules to Task 5's verdicts. **Hard constraints: `TRADING_MODE=paper` and `ENABLE_LIVE_TRADING=false` in `/etc/alpaca_bot/alpaca-bot.env` are not modified; no parameter is hand-applied outside the OOS-gated flow.**

- [ ] **Step 1: Apply the decision table**

The live strategy is breakout (`STRATEGY_VERSION=v1-breakout`).

| Task 5 verdict for the live strategy (breakout) | Action |
|---|---|
| negative-edge | `alpaca-bot-admin close-only --reason "breakout measured negative-edge at 5 bps (2026-06-12 audit)"` — uses the existing admin flow, which appends an AuditEvent. Entries stop; open positions can still close. |
| positive-edge | No halt. Candidate parameters flow ONLY through the existing nightly OOS gate (`alpaca-bot-nightly` → `candidate.env`); trigger a nightly run (`docker compose -f deploy/compose.yaml run --rm nightly`) and apply its `candidate.env` via the established candidate-apply flow if the OOS gate passes. |
| no-evidence / insufficient-data | No config change. Documented as "needs more data"; the bot keeps trading paper to generate live-session evidence. |

For non-live strategies: positive-edge verdicts are recorded in the report as promotion candidates (with their swept parameters) but are NOT switched into `STRATEGY_VERSION` in this cycle — switching the live strategy is a separate operator decision requiring its own plan-and-refine cycle. Negative-edge non-live strategies require no action (they are not running).

- [ ] **Step 2: Record the response**

Append the actions actually taken (including "none") to the `## Config response decided` section of `docs/strategy-audit/2026-06-12-honest-reevaluation.md`, with the exact commands run and any AuditEvent appended.

- [ ] **Step 3: Commit**

```bash
git add docs/strategy-audit/2026-06-12-honest-reevaluation.md
git commit -m "docs: record evidence-gated config response to 2026-06-12 audit

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Full code review — parallel agents (Phase 4)

Independent of Tasks 4–6; launch while the Task 5 audit runs. Per CLAUDE.md, dispatch both agents in a SINGLE message so they run concurrently.

- [ ] **Step 1: Launch two review agents in parallel**

Agent A — correctness (subagent_type `feature-dev:code-reviewer`):
> Review for logic errors in financial calculations, violations of the intent/dispatch separation, state mutations bypassing the audit log, and off-by-one/anchoring errors in indicator math. Files: `src/alpaca_bot/core/engine.py`, `src/alpaca_bot/risk/` (all), `src/alpaca_bot/runtime/order_dispatch.py`, `src/alpaca_bot/runtime/cycle_intent_execution.py`, `src/alpaca_bot/replay/` (all, post-fix). Report findings with severity (high/medium/low), file:line, and concrete evidence. Confidence-filter: report only issues you would stake a review approval on.

Agent B — security (subagent_type `general-purpose`, read-only instructions):
> Security audit of `src/alpaca_bot/execution/` (Alpaca API calls), `src/alpaca_bot/config/` (Settings, credential parsing), `src/alpaca_bot/admin/` (operator CLIs). Look for credential leakage (logs, exceptions, audit events), injection risks, auth bypass, and unsafe defaults when env vars are missing. Do NOT read or quote values from `/etc/alpaca_bot/alpaca-bot.env`. Report findings with severity, file:line, evidence.

- [ ] **Step 2: Consolidate findings**

Create `docs/reviews/2026-06-12-full-code-review.md`: scope, methodology (two parallel agents), severity-ranked findings table (severity, area, file:line, description, recommended action), and a closing section listing which findings (high severity only) get their own plan-and-refine cycle. **Do not fix findings ad hoc inside this plan.**

- [ ] **Step 3: Commit**

```bash
git add docs/reviews/2026-06-12-full-code-review.md
git commit -m "docs: full code + security review of trading-critical paths

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Completion checklist (Completionist mandate)

- [ ] All new tests pass; full suite green; no TODOs introduced.
- [ ] An off-watchlist nightly scenario replays with nonzero decisions (Task 5 Step 1 sanity check).
- [ ] Trend gate demonstrably varies within a scenario (Task 3 test).
- [ ] Every config action traceable to a Task 5 verdict; AuditEvents appended via existing flows only.
- [ ] `ENABLE_LIVE_TRADING=false`, `TRADING_MODE=paper` unchanged; no secrets in any commit.
- [ ] Review doc committed with severity-ranked findings; high-severity items queued as new plan-and-refine cycles, not patched ad hoc.
