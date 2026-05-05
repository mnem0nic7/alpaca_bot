# Position Limit Ceiling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-add a hard MAX_OPEN_POSITIONS ≤ 50 ceiling in Settings.validate() so the supervisor fails loudly at startup rather than silently entering hundreds of positions when the env file has a stale high value.

**Architecture:** One-line validation change in Settings.validate() (frozen dataclass) plus updating the existing test that was changed when the ceiling was removed. The ceiling is enforced at construction time — before the supervisor loop ever starts.

**Tech Stack:** Python frozen dataclass, pytest.

---

### Task 1: Re-add the MAX_OPEN_POSITIONS ceiling

**Files:**
- Modify: `tests/unit/test_momentum_strategy.py:285-288`
- Modify: `src/alpaca_bot/config/__init__.py:365-366`

- [ ] **Step 1: Update the existing test to assert the ceiling**

In `tests/unit/test_momentum_strategy.py`, replace the test at lines 285–288:

```python
def test_settings_validates_max_open_positions_upper_bound():
    # No upper ceiling — large values are allowed
    settings = _make_settings(max_open_positions=500)
    assert settings.max_open_positions == 500
```

with:

```python
def test_settings_validates_max_open_positions_upper_bound():
    with pytest.raises(ValueError, match="MAX_OPEN_POSITIONS"):
        _make_settings(max_open_positions=51)
    settings = _make_settings(max_open_positions=50)
    assert settings.max_open_positions == 50
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/unit/test_momentum_strategy.py::test_settings_validates_max_open_positions_upper_bound -v
```

Expected: FAIL — `_make_settings(max_open_positions=51)` returns a Settings without raising, so the `pytest.raises` context exits without the expected exception.

- [ ] **Step 3: Add the ceiling check in Settings.validate()**

In `src/alpaca_bot/config/__init__.py`, find the two-line block at lines 365–366:

```python
        if self.max_open_positions < 1:
            raise ValueError("MAX_OPEN_POSITIONS must be at least 1")
```

Change it to:

```python
        if self.max_open_positions < 1:
            raise ValueError("MAX_OPEN_POSITIONS must be at least 1")
        if self.max_open_positions > 50:
            raise ValueError("MAX_OPEN_POSITIONS must be at most 50")
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
pytest tests/unit/test_momentum_strategy.py::test_settings_validates_max_open_positions_upper_bound -v
```

Expected: PASS.

- [ ] **Step 5: Run the full test suite**

```bash
pytest tests/unit/ -q
```

Expected: all tests pass. The only test that touched `max_open_positions=500` was the one just updated; no other tests pass a value above 50.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/config/__init__.py tests/unit/test_momentum_strategy.py
git commit -m "fix: re-add MAX_OPEN_POSITIONS ≤ 50 ceiling to prevent silent over-allocation"
```

---

### Task 2: Deploy and remediate production

This task has no code changes — it is the operational follow-up that makes the fix effective.

- [ ] **Step 7: Update the production env file**

Edit `/etc/alpaca_bot/alpaca-bot.env`. Find the line:

```
MAX_OPEN_POSITIONS=500
```

Change it to:

```
MAX_OPEN_POSITIONS=20
```

- [ ] **Step 8: Redeploy**

```bash
./scripts/deploy.sh /etc/alpaca_bot/alpaca-bot.env
```

The migrate service runs first (no-op — no new migrations), then the supervisor restarts. Supervisor will now reject any future env file with `MAX_OPEN_POSITIONS > 50` at startup.

- [ ] **Step 9: Run the position remediation commands**

Wait for the supervisor container to be healthy, then:

```bash
# Preview: shows KEEP/CLOSE for all positions, ranked by stop_pct
alpaca-bot-admin close-excess --dry-run

# Execute: submits market exits for all positions ranked below top 20
alpaca-bot-admin close-excess

# Unblock stop orders: cancels all partially_filled entry orders at Alpaca
# and marks them canceled in DB so the dispatch loop can retry stops
alpaca-bot-admin cancel-partial-fills
```

After `cancel-partial-fills` completes, the supervisor's next cycle will submit stop orders for the 20 remaining positions without hitting error 40310000.
