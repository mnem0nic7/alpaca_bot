# Circuit Breaker Notification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `self._notifier.send()` in `_check_option_strategy_circuit_breakers` so the operator is immediately alerted when an option strategy is disabled by the circuit breaker.

**Architecture:** Single call-site addition following the existing supervisor.py notifier pattern (try/except, fire-and-forget, `if self._notifier is not None`). No new classes, no new config fields, no migrations.

**Tech Stack:** Python 3.12, existing `Notifier` protocol (`src/alpaca_bot/notifications/__init__.py`), pytest.

---

### Task 1: Red-phase tests

**Files:**
- Modify: `tests/unit/test_option_circuit_breaker.py` (append 4 new tests)

- [ ] **Step 1: Append 4 failing tests to the existing test file**

Add after the last test (`test_circuit_breaker_skipped_when_config_zero`):

```python
# ---------------------------------------------------------------------------
# Task 4: Supervisor circuit breaker notification
# ---------------------------------------------------------------------------


def _make_notifier():
    """Fake notifier that records (subject, body) tuples."""
    sent: list[tuple[str, str]] = []

    class _FakeNotifier:
        def send(self, subject: str, body: str) -> None:
            sent.append((subject, body))

    return _FakeNotifier(), sent


def test_circuit_breaker_sends_notification():
    """When a strategy is disabled, notifier receives subject with strategy name and body with P&L details."""
    supervisor, _, _ = _make_circuit_breaker_supervisor(
        rolling_pnl_by_strategy={"bear_orb": -600.0},
    )
    notifier, sent = _make_notifier()
    supervisor._notifier = notifier

    supervisor._check_option_strategy_circuit_breakers(
        session_date=date(2026, 5, 28),
        now=datetime(2026, 5, 28, 14, 0, tzinfo=timezone.utc),
    )

    assert len(sent) == 1
    subject, body = sent[0]
    assert "bear_orb" in subject
    assert "-600" in body or "-$600" in body or "600.00" in body
    assert "enable-strategy" in body
    assert "bear_orb" in body


def test_circuit_breaker_no_notification_when_already_disabled():
    """When strategy already disabled, notifier.send() is not called."""
    existing = StrategyFlag(
        strategy_name="bear_orb",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1-breakout",
        enabled=False,
    )
    supervisor, _, _ = _make_circuit_breaker_supervisor(
        rolling_pnl_by_strategy={"bear_orb": -600.0},
        existing_flag=existing,
    )
    notifier, sent = _make_notifier()
    supervisor._notifier = notifier

    supervisor._check_option_strategy_circuit_breakers(
        session_date=date(2026, 5, 28),
        now=datetime(2026, 5, 28, 14, 0, tzinfo=timezone.utc),
    )

    assert sent == []


def test_circuit_breaker_no_notification_when_notifier_none():
    """With _notifier=None, no AttributeError is raised when breaching threshold."""
    supervisor, saved_flags, _ = _make_circuit_breaker_supervisor(
        rolling_pnl_by_strategy={"bear_orb": -600.0},
    )
    supervisor._notifier = None  # explicitly None

    # Must not raise
    supervisor._check_option_strategy_circuit_breakers(
        session_date=date(2026, 5, 28),
        now=datetime(2026, 5, 28, 14, 0, tzinfo=timezone.utc),
    )
    assert len(saved_flags) == 1  # flag still written; notification just skipped


def test_circuit_breaker_notification_failure_does_not_crash_cycle():
    """When notifier.send() raises, _check_option_strategy_circuit_breakers() does not propagate."""
    supervisor, _, _ = _make_circuit_breaker_supervisor(
        rolling_pnl_by_strategy={"bear_orb": -600.0},
    )

    class _BrokenNotifier:
        def send(self, subject: str, body: str) -> None:
            raise RuntimeError("SMTP timeout")

    supervisor._notifier = _BrokenNotifier()

    # Must not raise
    supervisor._check_option_strategy_circuit_breakers(
        session_date=date(2026, 5, 28),
        now=datetime(2026, 5, 28, 14, 0, tzinfo=timezone.utc),
    )
```

- [ ] **Step 2: Run the 4 new tests to verify they fail with AttributeError or test assertion**

```bash
pytest tests/unit/test_option_circuit_breaker.py::test_circuit_breaker_sends_notification \
       tests/unit/test_option_circuit_breaker.py::test_circuit_breaker_no_notification_when_already_disabled \
       tests/unit/test_option_circuit_breaker.py::test_circuit_breaker_no_notification_when_notifier_none \
       tests/unit/test_option_circuit_breaker.py::test_circuit_breaker_notification_failure_does_not_crash_cycle \
       -v
```

Expected: `test_circuit_breaker_sends_notification` FAILS (assertion error — sent is empty)
`test_circuit_breaker_no_notification_when_already_disabled` PASSES (already no-op)
`test_circuit_breaker_no_notification_when_notifier_none` PASSES (notifier is None by default in helper)
`test_circuit_breaker_notification_failure_does_not_crash_cycle` FAILS (RuntimeError propagates)

- [ ] **Step 3: Commit the red-phase tests**

```bash
git add tests/unit/test_option_circuit_breaker.py
git commit -m "test(red): circuit breaker notification — 4 new failing tests"
```

---

### Task 2: Implement notification in _check_option_strategy_circuit_breakers

**Files:**
- Modify: `src/alpaca_bot/runtime/supervisor.py` (lines 1983–1990, after `logger.warning`)

- [ ] **Step 1: Add notification call after the logger.warning block**

In `_check_option_strategy_circuit_breakers`, locate the `logger.warning(...)` block ending at
line 1990. Immediately after the closing `)`, add:

```python
            if self._notifier is not None:
                try:
                    self._notifier.send(
                        subject=f"[alpaca-bot] Option circuit breaker: {strategy_name} disabled",
                        body=(
                            f"Strategy '{strategy_name}' has been automatically disabled by the "
                            f"rolling-loss circuit breaker.\n\n"
                            f"  Rolling P&L:  ${pnl:,.2f}\n"
                            f"  Threshold:    ${threshold:,.2f}\n"
                            f"  Window:       {window_days} days\n\n"
                            f"Re-enable via:\n"
                            f"  alpaca-bot-admin enable-strategy --strategy {strategy_name}"
                        ),
                    )
                except Exception:
                    logger.exception(
                        "Notifier failed to send circuit breaker alert for %s", strategy_name
                    )
```

The full updated loop body (from the `flag = StrategyFlag(...)` through the notification)
should look like:

```python
            flag = StrategyFlag(
                strategy_name=strategy_name,
                trading_mode=self.settings.trading_mode,
                strategy_version=self.settings.strategy_version,
                enabled=False,
                updated_at=now,
            )
            with store_lock if store_lock is not None else contextlib.nullcontext():
                flag_store.save(flag)  # commit=True (default): flag visible to _flag_store.load() later this cycle
            self._append_audit(
                AuditEvent(
                    event_type="option_strategy_circuit_breaker_triggered",
                    payload={
                        "strategy_name": strategy_name,
                        "rolling_pnl_usd": round(pnl, 2),
                        "threshold_usd": threshold,
                        "window_days": window_days,
                    },
                    created_at=now,
                )
            )
            logger.warning(
                "Option strategy %s disabled by circuit breaker: "
                "rolling P&L %.2f < threshold %.2f (window: %d days)",
                strategy_name,
                pnl,
                threshold,
                window_days,
            )
            if self._notifier is not None:
                try:
                    self._notifier.send(
                        subject=f"[alpaca-bot] Option circuit breaker: {strategy_name} disabled",
                        body=(
                            f"Strategy '{strategy_name}' has been automatically disabled by the "
                            f"rolling-loss circuit breaker.\n\n"
                            f"  Rolling P&L:  ${pnl:,.2f}\n"
                            f"  Threshold:    ${threshold:,.2f}\n"
                            f"  Window:       {window_days} days\n\n"
                            f"Re-enable via:\n"
                            f"  alpaca-bot-admin enable-strategy --strategy {strategy_name}"
                        ),
                    )
                except Exception:
                    logger.exception(
                        "Notifier failed to send circuit breaker alert for %s", strategy_name
                    )
```

- [ ] **Step 2: Run all 4 notification tests**

```bash
pytest tests/unit/test_option_circuit_breaker.py::test_circuit_breaker_sends_notification \
       tests/unit/test_option_circuit_breaker.py::test_circuit_breaker_no_notification_when_already_disabled \
       tests/unit/test_option_circuit_breaker.py::test_circuit_breaker_no_notification_when_notifier_none \
       tests/unit/test_option_circuit_breaker.py::test_circuit_breaker_notification_failure_does_not_crash_cycle \
       -v
```

Expected: All 4 PASS

- [ ] **Step 3: Run the full circuit breaker test file**

```bash
pytest tests/unit/test_option_circuit_breaker.py -v
```

Expected: All 14 tests PASS (10 existing + 4 new)

- [ ] **Step 4: Run the full test suite**

```bash
pytest
```

Expected: All tests pass (no regressions)

- [ ] **Step 5: Commit the implementation**

```bash
git add src/alpaca_bot/runtime/supervisor.py tests/unit/test_option_circuit_breaker.py
git commit -m "feat: notify operator when option strategy circuit breaker fires"
```
