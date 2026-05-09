# Daily Auto-Tune Activation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the "Last Backtest" dashboard panel (broken field mismatch), add a `nightly_sweep_completed` AuditEvent to the nightly CLI, and expand test coverage for both.

**Architecture:** `TuningResultStore.load_latest_best()` is extended to SELECT all seven metric columns and return them in its dict; the dashboard template then renders all fields (including score, Sharpe, run date, and winning params) with no changes needed to `MetricsSnapshot` or `service.py`. The nightly CLI writes a `nightly_sweep_completed` AuditEvent after the sweep loop, giving operators a timestamped record in the Admin History panel.

**Tech Stack:** Python 3.13, psycopg2-compatible fake-cursor pattern, Jinja2 template (dict key access works the same as attribute access), pytest monkeypatch.

---

## File Map

| File | Change |
|---|---|
| `src/alpaca_bot/storage/repositories.py` | `TuningResultStore.load_latest_best`: add `mean_return_pct`, `max_drawdown_pct`; expose `sharpe_ratio`, `created_at` |
| `src/alpaca_bot/web/templates/dashboard.html` | "Last Backtest" panel: add run date, score, Sharpe, params rows |
| `src/alpaca_bot/nightly/cli.py` | Import `AuditEvent`, `AuditEventStore`; write `nightly_sweep_completed` event after sweep |
| `tests/unit/test_tuning_result_store.py` | New — assert all 7 fields present in `load_latest_best` result |
| `tests/unit/test_nightly_cli.py` | Extend — assert `nightly_sweep_completed` AuditEvent written |

---

### Task 1: Fix `load_latest_best` — add missing metric fields

**Context:** The DB table `tuning_results` has columns `mean_return_pct` and `max_drawdown_pct` but
`load_latest_best()` does not SELECT them. The dashboard template references both, so they always
render as `—`. Fix: update the SELECT and returned dict.

**Files:**
- Modify: `src/alpaca_bot/storage/repositories.py:1297-1320`
- Test: `tests/unit/test_tuning_result_store.py` (new file)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_tuning_result_store.py`:

```python
from __future__ import annotations

import json
from datetime import datetime, timezone


class _FakeCursor:
    def __init__(self, connection: "_FakeConn") -> None:
        self._conn = connection

    def execute(self, sql: str, params=None) -> None:
        self._conn.executed.append((sql, params))

    def fetchone(self):
        return self._conn.responses.pop(0) if self._conn.responses else None

    def fetchall(self):
        if not self._conn.responses:
            return []
        r = self._conn.responses.pop(0)
        return r if isinstance(r, list) else [r]


class _FakeConn:
    def __init__(self, responses=()) -> None:
        self.responses = list(responses)
        self.executed: list = []

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


def test_load_latest_best_returns_all_fields() -> None:
    """load_latest_best must return all 7 fields including mean_return_pct and max_drawdown_pct."""
    from alpaca_bot.storage.repositories import TuningResultStore

    created = datetime(2026, 5, 9, 22, 35, tzinfo=timezone.utc)
    params = {"BREAKOUT_LOOKBACK_BARS": "20", "RELATIVE_VOLUME_THRESHOLD": "1.5"}
    row = (
        json.dumps(params),  # params (JSONB returned as str)
        0.52,               # score
        15,                 # total_trades
        0.60,               # win_rate
        0.018,              # mean_return_pct
        0.045,              # max_drawdown_pct
        1.3,                # sharpe_ratio
        created,            # created_at
    )
    conn = _FakeConn(responses=[row])
    store = TuningResultStore(conn)

    result = store.load_latest_best(trading_mode="paper")

    assert result is not None
    assert result["params"] == params
    assert result["score"] == 0.52
    assert result["total_trades"] == 15
    assert result["win_rate"] == 0.60
    assert result["mean_return_pct"] == 0.018
    assert result["max_drawdown_pct"] == 0.045
    assert result["sharpe_ratio"] == 1.3
    assert result["created_at"] == created


def test_load_latest_best_returns_none_when_no_rows() -> None:
    """load_latest_best returns None when no is_best rows exist."""
    from alpaca_bot.storage.repositories import TuningResultStore

    conn = _FakeConn(responses=[None])
    store = TuningResultStore(conn)

    result = store.load_latest_best(trading_mode="paper")

    assert result is None


def test_load_latest_best_parses_dict_params_directly() -> None:
    """load_latest_best handles params returned as a Python dict (psycopg2 JSONB auto-decode)."""
    from alpaca_bot.storage.repositories import TuningResultStore

    created = datetime(2026, 5, 9, 22, 35, tzinfo=timezone.utc)
    params = {"DAILY_SMA_PERIOD": "20"}
    row = (
        params,    # params already a dict (psycopg2 JSONB → dict)
        0.3,       # score
        5,         # total_trades
        0.4,       # win_rate
        None,      # mean_return_pct (null in DB)
        None,      # max_drawdown_pct (null in DB)
        None,      # sharpe_ratio (null in DB)
        created,   # created_at
    )
    conn = _FakeConn(responses=[row])
    store = TuningResultStore(conn)

    result = store.load_latest_best(trading_mode="paper")

    assert result is not None
    assert result["params"] == params
    assert result["mean_return_pct"] is None
    assert result["max_drawdown_pct"] is None
    assert result["sharpe_ratio"] is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_tuning_result_store.py -v
```

Expected: FAIL — `test_load_latest_best_returns_all_fields` fails because the SELECT returns 6
columns but the test stub returns 8, and `mean_return_pct` / `max_drawdown_pct` are not in the
result dict.

- [ ] **Step 3: Fix `load_latest_best` in `repositories.py`**

Find the `load_latest_best` method at line ~1297 in
`src/alpaca_bot/storage/repositories.py`. Replace the entire method body:

```python
def load_latest_best(self, *, trading_mode: str) -> dict | None:
    """Return the most recent is_best=TRUE row as a plain dict, or None."""
    row = fetch_one(
        self._connection,
        """
        SELECT params, score, total_trades, win_rate,
               mean_return_pct, max_drawdown_pct, sharpe_ratio, created_at
        FROM tuning_results
        WHERE trading_mode = %s AND is_best = TRUE
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (trading_mode,),
    )
    if row is None:
        return None
    params = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    return {
        "params": params,
        "score": row[1],
        "total_trades": row[2],
        "win_rate": row[3],
        "mean_return_pct": row[4],
        "max_drawdown_pct": row[5],
        "sharpe_ratio": row[6],
        "created_at": row[7],
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_tuning_result_store.py -v
```

Expected: 3 tests PASS.

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
pytest tests/unit/ -x -q
```

Expected: all existing tests continue to pass.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/storage/repositories.py tests/unit/test_tuning_result_store.py
git commit -m "fix: load_latest_best returns mean_return_pct, max_drawdown_pct, sharpe_ratio, created_at"
```

---

### Task 2: Enhance the "Last Backtest" dashboard panel

**Context:** The panel currently shows 4 rows (total_trades, win_rate, mean_return_pct,
max_drawdown_pct). After Task 1, `mean_return_pct` and `max_drawdown_pct` will actually render.
Now add the remaining fields the dict provides: run date, score, Sharpe, and the winning params.
`last_backtest` is a plain dict; Jinja2 accesses dict keys with dot notation the same as attributes.

**Files:**
- Modify: `src/alpaca_bot/web/templates/dashboard.html:880-895`

- [ ] **Step 1: Replace the "Last Backtest" panel body**

Find the existing panel at line ~880 in
`src/alpaca_bot/web/templates/dashboard.html`. Replace the entire `<div class="panel">` block
(from `<div class="panel">` through the closing `</div>`) with:

```html
        <div class="panel">
          <h2>Last Backtest</h2>
          {% if metrics.last_backtest %}
            <p class="muted" style="margin-bottom: 0.75rem;">
              Run: {{ format_timestamp(metrics.last_backtest.created_at) }}
            </p>
            <table class="data-table">
              <thead><tr><th>Metric</th><th>Value</th></tr></thead>
              <tbody>
                <tr><td>Score</td><td>{{ "%.3f" | format(metrics.last_backtest.score) if metrics.last_backtest.score is not none else "—" }}</td></tr>
                <tr><td>Total trades</td><td>{{ metrics.last_backtest.total_trades }}</td></tr>
                <tr><td>Win rate</td><td>{{ "%.0f" | format(metrics.last_backtest.win_rate * 100) if metrics.last_backtest.win_rate is not none else "—" }}%</td></tr>
                <tr><td>Mean return</td><td>{{ "%.2f" | format(metrics.last_backtest.mean_return_pct * 100) if metrics.last_backtest.mean_return_pct is not none else "—" }}%</td></tr>
                <tr><td>Max drawdown</td><td>{{ "%.2f" | format(metrics.last_backtest.max_drawdown_pct * 100) if metrics.last_backtest.max_drawdown_pct is not none else "—" }}%</td></tr>
                <tr><td>Sharpe</td><td>{{ "%.2f" | format(metrics.last_backtest.sharpe_ratio) if metrics.last_backtest.sharpe_ratio is not none else "—" }}</td></tr>
              </tbody>
            </table>
            {% if metrics.last_backtest.params %}
              <p class="muted" style="margin-top: 0.75rem; font-size: 0.85em;">
                <strong>Winning params:</strong><br>
                {% for k, v in metrics.last_backtest.params.items() %}
                  <span class="mono">{{ k }}={{ v }}</span>{% if not loop.last %}, {% endif %}
                {% endfor %}
              </p>
            {% endif %}
          {% else %}
            <p class="muted">No backtest data.</p>
          {% endif %}
        </div>
```

- [ ] **Step 2: Run the web tests to verify no regressions**

```bash
pytest tests/unit/test_web_app.py -v -q
```

Expected: all web tests pass. (The template change is additive — existing test assertions remain
valid. Tests that check the full rendered HTML will still pass because the `last_backtest` fixture
in those tests returns `None`, so the panel shows "No backtest data." unchanged.)

- [ ] **Step 3: Commit**

```bash
git add src/alpaca_bot/web/templates/dashboard.html
git commit -m "feat: enhance Last Backtest panel with run date, score, Sharpe, and winning params"
```

---

### Task 3: Write `nightly_sweep_completed` AuditEvent

**Context:** The nightly CLI has no audit trail. When the cron fires and applies new params,
there's no `AuditEvent` row so the "Admin History" panel on the dashboard shows nothing. Fix:
append a `nightly_sweep_completed` event after the strategy sweep loop completes (whether or not
any winner was found), using the existing `AuditEventStore`.

**Files:**
- Modify: `src/alpaca_bot/nightly/cli.py`
- Modify: `tests/unit/test_nightly_cli.py` (add one test)

- [ ] **Step 1: Write the failing test**

Add this test to the **bottom** of `tests/unit/test_nightly_cli.py`:

```python
def test_nightly_cli_writes_audit_event_after_sweep(monkeypatch, tmp_path):
    """nightly_sweep_completed AuditEvent is written after the strategy sweep, win or lose."""
    from alpaca_bot.nightly import cli as module
    from alpaca_bot.tuning.sweep import TuningCandidate

    _patch_env(monkeypatch)
    _make_scenario_files(tmp_path)
    _patch_common_db(monkeypatch, module)
    monkeypatch.setattr(module, "split_scenario", _fake_split)

    cand = TuningCandidate(params={"BREAKOUT_LOOKBACK_BARS": "20"}, report=None, score=0.5)
    monkeypatch.setattr(module, "run_multi_scenario_sweep", lambda **kw: [cand])
    monkeypatch.setattr(module, "evaluate_candidates_oos",
                        lambda candidates, oos_scenarios, **kw: [0.4])

    appended_events: list = []

    class FakeAuditEventStore:
        def __init__(self, conn): pass
        def append(self, event, *, commit=True):
            appended_events.append(event)

    monkeypatch.setattr(module, "AuditEventStore", FakeAuditEventStore)

    output_env = tmp_path / "candidate.env"
    monkeypatch.setattr(sys, "argv", [
        "nightly", "--dry-run", "--no-db",
        "--output-dir", str(tmp_path),
        "--output-env", str(output_env),
        "--strategies", "breakout",
    ])

    result = module.main()

    assert result == 0
    sweep_events = [e for e in appended_events if e.event_type == "nightly_sweep_completed"]
    assert len(sweep_events) == 1, f"expected 1 nightly_sweep_completed event, got {len(sweep_events)}"
    payload = sweep_events[0].payload
    assert payload["strategy_count"] == 1
    assert payload["candidates_accepted"] == 1
    assert payload["best_strategy"] == "breakout"
    assert payload["candidate_env_written"] is True
    assert "best_score" in payload
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_nightly_cli.py::test_nightly_cli_writes_audit_event_after_sweep -v
```

Expected: FAIL with `AttributeError: module 'alpaca_bot.nightly.cli' has no attribute 'AuditEventStore'`.

- [ ] **Step 3: Add imports and AuditEvent write to `nightly/cli.py`**

**3a. Add imports** at the top of `src/alpaca_bot/nightly/cli.py`.

The current storage imports block looks like:
```python
from alpaca_bot.storage.db import connect_postgres
from alpaca_bot.storage.models import EQUITY_SESSION_STATE_STRATEGY_NAME
from alpaca_bot.storage.repositories import (
    DailySessionStateStore,
    OrderStore,
    TuningResultStore,
    WatchlistStore,
)
```

Replace it with:
```python
from alpaca_bot.storage.db import connect_postgres
from alpaca_bot.storage.models import EQUITY_SESSION_STATE_STRATEGY_NAME, AuditEvent
from alpaca_bot.storage.repositories import (
    AuditEventStore,
    DailySessionStateStore,
    OrderStore,
    TuningResultStore,
    WatchlistStore,
)
```

**3b. Write the AuditEvent after the sweep.** Find the block in `main()` that looks like:

```python
            if winners:
                composite_params = _build_composite_env(winners)
                env_block = _format_composite_env_block(composite_params, winners[0][0], now)
                print(f"\n{env_block}")
                if args.output_env:
                    Path(args.output_env).write_text(env_block + "\n")
                    print(f"Candidate env written to {args.output_env}")
            else:
                print("\nNo walk-forward held candidates across all strategies — current parameters remain active.")
```

Replace it with:

```python
            env_written = False
            if winners:
                composite_params = _build_composite_env(winners)
                env_block = _format_composite_env_block(composite_params, winners[0][0], now)
                print(f"\n{env_block}")
                if args.output_env:
                    Path(args.output_env).write_text(env_block + "\n")
                    print(f"Candidate env written to {args.output_env}")
                    env_written = True
            else:
                print("\nNo walk-forward held candidates across all strategies — current parameters remain active.")

            best_strat = winners[0][0] if winners else None
            best_score = winners[0][2] if winners else None
            try:
                AuditEventStore(conn).append(
                    AuditEvent(
                        event_type="nightly_sweep_completed",
                        payload={
                            "strategy_count": len(strategy_names),
                            "candidates_accepted": len(winners),
                            "best_strategy": best_strat,
                            "best_score": best_score,
                            "candidate_env_written": env_written,
                        },
                        created_at=now,
                    )
                )
            except Exception as exc:
                print(f"Warning: could not write nightly_sweep_completed audit event: {exc}",
                      file=sys.stderr)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/unit/test_nightly_cli.py::test_nightly_cli_writes_audit_event_after_sweep -v
```

Expected: PASS.

- [ ] **Step 5: Run the full nightly test suite to check for regressions**

```bash
pytest tests/unit/test_nightly_cli.py -v
```

Expected: all 12+ tests pass.

- [ ] **Step 6: Run the full test suite**

```bash
pytest tests/unit/ -x -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/nightly/cli.py tests/unit/test_nightly_cli.py
git commit -m "feat: write nightly_sweep_completed AuditEvent after strategy sweep"
```

---

### Task 4: Final verification

- [ ] **Step 1: Run full test suite one last time**

```bash
pytest tests/unit/ -q
```

Expected: all tests pass, no warnings about missing fields.

- [ ] **Step 2: Verify the "Last Backtest" panel renders with real data (optional smoke-check)**

If `tuning_results` has rows (after the cron has run once), start the dashboard and confirm the
panel shows score, Sharpe, run date, and params. If no rows exist yet (tuning_results is empty),
confirm the panel shows "No backtest data." — which is correct until the first nightly run.

```bash
alpaca-bot-web &
curl -s http://localhost:18080/ | grep -i "last backtest"
```

Expected: the heading renders. No 500 errors.

- [ ] **Step 3: Tag the work done**

No migration needed. No new env vars. All three changes (repositories, template, nightly CLI) are
self-contained and independently tested.
