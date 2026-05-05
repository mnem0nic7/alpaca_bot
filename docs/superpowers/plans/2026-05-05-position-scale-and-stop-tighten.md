# Position Scale-Up and Stop Tighten — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce `ATR_STOP_MULTIPLIER` from 1.5 to 1.0 and scale `MAX_OPEN_POSITIONS` from 3 to 20, with proportionally adjusted risk parameters (`MAX_POSITION_PCT` 5%→1.5%, `MAX_PORTFOLIO_EXPOSURE_PCT` 15%→30%).

**Architecture:** Two-file change. (1) Six default value updates in `config/__init__.py` — four in `from_env()` fallback strings and two dataclass field defaults. (2) Four value updates and one new line in `DEPLOYMENT.md`'s env-file template. No migrations, no new env vars, no behavioral code changes. One new test verifies new defaults are parsed from a minimal env.

**Tech Stack:** Python frozen dataclass, pytest, env-var-based configuration.

---

### Task 1: Add failing test

**Files:**
- Modify: `tests/unit/test_momentum_strategy.py` — add one test after `test_settings_rejects_max_position_pct_exceeding_portfolio_exposure`

- [ ] **Step 1: Write the failing test**

Add this test at the end of `tests/unit/test_momentum_strategy.py`:

```python
def test_settings_new_production_defaults_from_env() -> None:
    """ATR_STOP_MULTIPLIER, MAX_OPEN_POSITIONS, MAX_POSITION_PCT, and
    MAX_PORTFOLIO_EXPOSURE_PCT must use the new production defaults when
    the env vars are absent."""
    from alpaca_bot.config import Settings
    from tests.unit.helpers import _base_env

    env = _base_env()
    # _base_env() explicitly sets these two; pop them so from_env falls through to defaults
    env.pop("MAX_POSITION_PCT")
    env.pop("MAX_OPEN_POSITIONS")
    # ATR_STOP_MULTIPLIER and MAX_PORTFOLIO_EXPOSURE_PCT are not set by _base_env()

    settings = Settings.from_env(env)

    assert settings.atr_stop_multiplier == 1.0
    assert settings.max_open_positions == 20
    assert settings.max_position_pct == 0.015
    assert settings.max_portfolio_exposure_pct == 0.30
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_momentum_strategy.py::test_settings_new_production_defaults_from_env -v
```

Expected: FAIL — `AssertionError: assert 1.5 == 1.0` (or similar for any of the four assertions, since all four defaults are still the old values).

- [ ] **Step 3: Commit failing test**

```bash
git add tests/unit/test_momentum_strategy.py
git commit -m "test: add failing test for new production defaults (ATR 1.0, positions 20)"
```

---

### Task 2: Implement config changes

**Files:**
- Modify: `src/alpaca_bot/config/__init__.py`
  - Line 76: dataclass default `max_portfolio_exposure_pct`
  - Line 83: dataclass default `atr_stop_multiplier`
  - Line 168: `from_env()` fallback for `MAX_POSITION_PCT`
  - Line 169: `from_env()` fallback for `MAX_OPEN_POSITIONS`
  - Line 172: `from_env()` fallback for `MAX_PORTFOLIO_EXPOSURE_PCT`
  - Line 182: `from_env()` fallback for `ATR_STOP_MULTIPLIER`

- [ ] **Step 4: Change the two dataclass defaults**

In `src/alpaca_bot/config/__init__.py`, find line 76:

```python
    max_portfolio_exposure_pct: float = 0.15
```

Change to:

```python
    max_portfolio_exposure_pct: float = 0.30
```

Then find line 83:

```python
    atr_stop_multiplier: float = 1.5
```

Change to:

```python
    atr_stop_multiplier: float = 1.0
```

- [ ] **Step 5: Change the four `from_env()` fallback strings**

In `src/alpaca_bot/config/__init__.py`, find line 168:

```python
            max_position_pct=float(values.get("MAX_POSITION_PCT", "0.05")),
            max_open_positions=int(values.get("MAX_OPEN_POSITIONS", "3")),
```

Change to:

```python
            max_position_pct=float(values.get("MAX_POSITION_PCT", "0.015")),
            max_open_positions=int(values.get("MAX_OPEN_POSITIONS", "20")),
```

Then find line 171–173:

```python
            max_portfolio_exposure_pct=float(
                values.get("MAX_PORTFOLIO_EXPOSURE_PCT", "0.15")
            ),
```

Change to:

```python
            max_portfolio_exposure_pct=float(
                values.get("MAX_PORTFOLIO_EXPOSURE_PCT", "0.30")
            ),
```

Then find line 182:

```python
            atr_stop_multiplier=float(values.get("ATR_STOP_MULTIPLIER", "1.5")),
```

Change to:

```python
            atr_stop_multiplier=float(values.get("ATR_STOP_MULTIPLIER", "1.0")),
```

- [ ] **Step 6: Run the new test to verify it passes**

```bash
pytest tests/unit/test_momentum_strategy.py::test_settings_new_production_defaults_from_env -v
```

Expected: PASS.

- [ ] **Step 7: Run full test suite**

```bash
pytest tests/unit/ -q
```

Expected: all tests pass. The tests in `_base_env()`-based fixtures are unaffected — they explicitly set `MAX_POSITION_PCT` and `MAX_OPEN_POSITIONS` and don't rely on defaults.

- [ ] **Step 8: Commit**

```bash
git add src/alpaca_bot/config/__init__.py
git commit -m "feat: tighten stop loss and scale max positions (ATR 1.5→1.0, positions 3→20, risk params recalibrated)"
```

---

### Task 3: Update DEPLOYMENT.md

**Files:**
- Modify: `DEPLOYMENT.md` — update env template values and add `MAX_PORTFOLIO_EXPOSURE_PCT`

- [ ] **Step 9: Update env template**

In `DEPLOYMENT.md`, find lines 47–48:

```
MAX_POSITION_PCT=0.05
MAX_OPEN_POSITIONS=3
```

Change to:

```
MAX_POSITION_PCT=0.015
MAX_OPEN_POSITIONS=20
MAX_PORTFOLIO_EXPOSURE_PCT=0.30
```

Then find line 53:

```
ATR_STOP_MULTIPLIER=1.5
```

Change to:

```
ATR_STOP_MULTIPLIER=1.0
```

- [ ] **Step 10: Commit**

```bash
git add DEPLOYMENT.md
git commit -m "docs: update DEPLOYMENT.md env template for new risk defaults"
```

---

### Deployment note

The running production env file (`/etc/alpaca_bot/alpaca-bot.env`) overrides Python defaults. After deploying the code, manually update that file with the new values and redeploy:

```bash
# Edit /etc/alpaca_bot/alpaca-bot.env — update these four lines:
# MAX_POSITION_PCT=0.015
# MAX_OPEN_POSITIONS=20
# MAX_PORTFOLIO_EXPOSURE_PCT=0.30
# ATR_STOP_MULTIPLIER=1.0

./scripts/deploy.sh /etc/alpaca_bot/alpaca-bot.env
```
