from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.execution import BrokerOrder, BrokerPosition
from alpaca_bot.notifications import CompositeNotifier, LOG_ONLY, Notifier
from alpaca_bot.runtime.startup_recovery import recover_startup_state
from alpaca_bot.storage import AuditEvent, OrderRecord, PositionRecord


# ---------------------------------------------------------------------------
# RecordingNotifier — shared test double
# ---------------------------------------------------------------------------


class RecordingNotifier:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def send(self, subject: str, body: str) -> None:
        self.calls.append((subject, body))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_settings() -> Settings:
    return Settings.from_env(
        {
            "TRADING_MODE": "paper",
            "ENABLE_LIVE_TRADING": "false",
            "STRATEGY_VERSION": "v1-breakout",
            "DATABASE_URL": "postgresql://test/db",
            "MARKET_DATA_FEED": "sip",
            "SYMBOLS": "AAPL",
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
        }
    )


def _make_runtime(
    positions: list[PositionRecord] | None = None,
    orders: list[OrderRecord] | None = None,
):
    events: list[AuditEvent] = []

    def _list_all(**_):
        return list(positions or [])

    def _replace_all(**_):
        pass

    def _list_by_status(**_):
        return list(orders or [])

    def _save(_, *, commit=True):
        pass

    return SimpleNamespace(
        position_store=SimpleNamespace(
            list_all=_list_all,
            replace_all=_replace_all,
        ),
        order_store=SimpleNamespace(
            list_by_status=_list_by_status,
            save=_save,
        ),
        audit_event_store=SimpleNamespace(append=lambda e, *, commit=True: events.append(e)),
        connection=SimpleNamespace(commit=lambda: None),
        _events=events,
    )


# ---------------------------------------------------------------------------
# CompositeNotifier
# ---------------------------------------------------------------------------


class TestCompositeNotifier:
    def test_calls_all_notifiers(self):
        a, b = RecordingNotifier(), RecordingNotifier()
        CompositeNotifier([a, b]).send("subj", "body")
        assert a.calls == [("subj", "body")]
        assert b.calls == [("subj", "body")]

    def test_continues_after_failing_notifier(self):
        class FailingNotifier:
            def send(self, subject: str, body: str) -> None:
                raise RuntimeError("SMTP down")

        good = RecordingNotifier()
        CompositeNotifier([FailingNotifier(), good]).send("s", "b")
        assert good.calls == [("s", "b")]

    def test_empty_composite_sends_nothing(self):
        CompositeNotifier([]).send("s", "b")  # must not raise


# ---------------------------------------------------------------------------
# Settings validation for email vars
# ---------------------------------------------------------------------------


class TestSettingsEmailValidation:
    def _env(self, **overrides: str) -> dict[str, str]:
        base = {
            "TRADING_MODE": "paper",
            "ENABLE_LIVE_TRADING": "false",
            "STRATEGY_VERSION": "v1",
            "DATABASE_URL": "postgresql://test/db",
            "MARKET_DATA_FEED": "sip",
            "SYMBOLS": "AAPL",
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
        }
        base.update(overrides)
        return base

    def test_all_email_vars_set_is_valid(self):
        env = self._env(
            NOTIFY_EMAIL_FROM="bot@example.com",
            NOTIFY_EMAIL_TO="op@example.com",
            NOTIFY_SMTP_HOST="smtp.example.com",
            NOTIFY_SMTP_USER="user",
            NOTIFY_SMTP_PASSWORD="pass",
        )
        s = Settings.from_env(env)
        assert s.notify_email_from == "bot@example.com"
        assert s.notify_smtp_port == 587

    def test_partial_email_vars_raises(self):
        env = self._env(NOTIFY_EMAIL_FROM="bot@example.com")
        with pytest.raises(ValueError, match="NOTIFY_EMAIL_TO"):
            Settings.from_env(env)

    def test_slack_webhook_no_email_is_valid(self):
        env = self._env(SLACK_WEBHOOK_URL="https://hooks.slack.com/xxx")
        s = Settings.from_env(env)
        assert s.slack_webhook_url == "https://hooks.slack.com/xxx"

    def test_no_notification_vars_valid(self):
        s = Settings.from_env(self._env())
        assert s.slack_webhook_url is None
        assert s.notify_email_from is None


# ---------------------------------------------------------------------------
# Trigger point: recover_startup_state sends notification on mismatch
# ---------------------------------------------------------------------------


class TestStartupRecoveryNotifier:
    def test_notifier_called_on_mismatch(self):
        """When broker has a position not tracked locally, notifier fires."""
        settings = make_settings()
        notifier = RecordingNotifier()
        broker_positions = [
            BrokerPosition(symbol="AAPL", quantity=10, entry_price=150.0)
        ]
        runtime = _make_runtime(positions=[])  # no local position

        recover_startup_state(
            settings=settings,
            runtime=runtime,
            broker_open_positions=broker_positions,
            broker_open_orders=[],
            notifier=notifier,
        )

        assert len(notifier.calls) == 1
        subject, body = notifier.calls[0]
        assert "mismatch" in subject.lower()
        assert "AAPL" in body

    def test_notifier_not_called_when_no_mismatches(self):
        """Clean startup: no positions, no orders → notifier stays silent."""
        settings = make_settings()
        notifier = RecordingNotifier()
        runtime = _make_runtime(positions=[])

        recover_startup_state(
            settings=settings,
            runtime=runtime,
            broker_open_positions=[],
            broker_open_orders=[],
            notifier=notifier,
        )

        assert notifier.calls == []

    def test_none_notifier_does_not_raise(self):
        """Omitting the notifier (default None) must not raise on mismatch."""
        settings = make_settings()
        broker_positions = [BrokerPosition(symbol="AAPL", quantity=10, entry_price=150.0)]
        runtime = _make_runtime(positions=[])

        recover_startup_state(
            settings=settings,
            runtime=runtime,
            broker_open_positions=broker_positions,
            broker_open_orders=[],
            notifier=None,
        )


# ---------------------------------------------------------------------------
# Trigger point: apply_trade_update sends notification on stop/exit fill
# ---------------------------------------------------------------------------


class TestTradeUpdateNotifier:
    def _make_order(self, intent_type: str = "stop") -> OrderRecord:
        return OrderRecord(
            client_order_id="client-1",
            symbol="AAPL",
            side="sell",
            intent_type=intent_type,
            status="new",
            quantity=10,
            trading_mode=TradingMode.PAPER,
            strategy_version="v1-breakout",
            created_at=datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc),
        )

    def _make_runtime(self, order: OrderRecord):
        events: list[AuditEvent] = []
        positions_deleted: list[str] = []

        return SimpleNamespace(
            order_store=SimpleNamespace(
                load=lambda client_order_id: order if client_order_id == order.client_order_id else None,
                load_by_broker_order_id=lambda _: None,
                save=lambda _, **__: None,
            ),
            position_store=SimpleNamespace(
                save=lambda _, **__: None,
                delete=lambda **_: positions_deleted.append("deleted"),
            ),
            audit_event_store=SimpleNamespace(append=lambda e, **__: events.append(e)),
            connection=SimpleNamespace(commit=lambda: None),
            _events=events,
            _deleted=positions_deleted,
        )

    def _stop_fill_update(self, intent_type: str = "stop", filled_avg_price: float = 148.0) -> dict:
        return {
            "event": "fill",
            "client_order_id": "client-1",
            "broker_order_id": "broker-1",
            "symbol": "AAPL",
            "side": "sell",
            "status": "filled",
            "qty": 10,
            "filled_qty": 10,
            "filled_avg_price": filled_avg_price,
            "timestamp": datetime(2026, 4, 25, 14, 30, tzinfo=timezone.utc).isoformat(),
        }

    def test_stop_fill_triggers_notification(self):
        from alpaca_bot.runtime.trade_updates import apply_trade_update

        order = self._make_order("stop")
        runtime = self._make_runtime(order)
        notifier = RecordingNotifier()
        settings = make_settings()

        apply_trade_update(
            settings=settings,
            runtime=runtime,
            update=self._stop_fill_update(),
            notifier=notifier,
        )

        assert len(notifier.calls) == 1
        subject, body = notifier.calls[0]
        assert "AAPL" in subject
        assert "148.0" in body

    def test_entry_fill_sends_fill_notification(self):
        from alpaca_bot.runtime.trade_updates import apply_trade_update

        order = self._make_order("entry")
        runtime = self._make_runtime(order)
        notifier = RecordingNotifier()
        settings = make_settings()

        update = {
            "event": "fill",
            "client_order_id": "client-1",
            "broker_order_id": "broker-1",
            "symbol": "AAPL",
            "side": "buy",
            "status": "filled",
            "qty": 10,
            "filled_qty": 10,
            "filled_avg_price": 150.0,
            "timestamp": datetime(2026, 4, 25, 10, 30, tzinfo=timezone.utc).isoformat(),
        }

        apply_trade_update(
            settings=settings,
            runtime=runtime,
            update=update,
            notifier=notifier,
        )

        assert len(notifier.calls) == 1
        subject, body = notifier.calls[0]
        assert "AAPL" in subject
        assert "150.0" in subject or "150.0" in body

    def test_none_notifier_on_stop_fill_does_not_raise(self):
        from alpaca_bot.runtime.trade_updates import apply_trade_update

        order = self._make_order("stop")
        runtime = self._make_runtime(order)
        settings = make_settings()

        apply_trade_update(
            settings=settings,
            runtime=runtime,
            update=self._stop_fill_update(),
            notifier=None,
        )


# ---------------------------------------------------------------------------
# Trigger point: run_admin_command notifies on halt and close-only
# ---------------------------------------------------------------------------


class TestAdminCliNotifier:
    def _make_connection(self):
        events: list[AuditEvent] = []
        statuses: list[object] = []

        cursor_stub = SimpleNamespace(
            execute=lambda sql, params=None: None,
            fetchone=lambda: None,
            fetchall=lambda: [],
        )

        return SimpleNamespace(
            cursor=lambda: cursor_stub,
            commit=lambda: None,
        )

    def test_halt_sends_notification(self):
        from alpaca_bot.admin.cli import run_admin_command
        from alpaca_bot.config import Settings

        settings = make_settings()
        notifier = RecordingNotifier()
        conn = self._make_connection()

        run_admin_command(
            ["halt", "--reason", "manual stop"],
            settings=settings,
            connection=conn,
            notifier=notifier,
            now=datetime(2026, 4, 25, 15, 0, tzinfo=timezone.utc),
        )

        assert len(notifier.calls) == 1
        subject, body = notifier.calls[0]
        assert "halt" in subject.lower()
        assert "manual stop" in body

    def test_close_only_sends_notification(self):
        from alpaca_bot.admin.cli import run_admin_command

        settings = make_settings()
        notifier = RecordingNotifier()
        conn = self._make_connection()

        run_admin_command(
            ["close-only", "--reason", "eod"],
            settings=settings,
            connection=conn,
            notifier=notifier,
            now=datetime(2026, 4, 25, 15, 0, tzinfo=timezone.utc),
        )

        assert len(notifier.calls) == 1
        subject, _ = notifier.calls[0]
        assert "close" in subject.lower()

    def test_resume_sends_notification(self):
        from alpaca_bot.admin.cli import run_admin_command

        settings = make_settings()
        notifier = RecordingNotifier()
        conn = self._make_connection()

        run_admin_command(
            ["resume"],
            settings=settings,
            connection=conn,
            notifier=notifier,
            now=datetime(2026, 4, 25, 15, 0, tzinfo=timezone.utc),
        )

        assert len(notifier.calls) == 1
        subject, _ = notifier.calls[0]
        assert "resume" in subject.lower()

    def test_none_notifier_does_not_raise_on_halt(self):
        from alpaca_bot.admin.cli import run_admin_command

        settings = make_settings()
        conn = self._make_connection()

        run_admin_command(
            ["halt", "--reason", "test"],
            settings=settings,
            connection=conn,
            notifier=None,
            now=datetime(2026, 4, 25, 15, 0, tzinfo=timezone.utc),
        )


# ---------------------------------------------------------------------------
# build_notifier factory
# ---------------------------------------------------------------------------


class TestBuildNotifier:
    from types import SimpleNamespace as _NS

    def _settings(self, **kwargs: object) -> object:
        from types import SimpleNamespace
        return SimpleNamespace(**kwargs)

    def test_no_channels_returns_log_only(self):
        from alpaca_bot.notifications.factory import build_notifier

        notifier = build_notifier(self._settings())
        assert notifier is LOG_ONLY

    def test_slack_only_returns_slack_notifier(self):
        from alpaca_bot.notifications.factory import build_notifier
        from alpaca_bot.notifications.slack import SlackNotifier

        notifier = build_notifier(self._settings(slack_webhook_url="https://hooks.slack.com/x"))
        assert isinstance(notifier, SlackNotifier)

    def test_email_only_returns_email_notifier(self):
        from alpaca_bot.notifications.factory import build_notifier
        from alpaca_bot.notifications.email import EmailNotifier

        notifier = build_notifier(
            self._settings(
                notify_smtp_host="smtp.example.com",
                notify_smtp_port=587,
                notify_smtp_user="user",
                notify_smtp_password="pass",
                notify_email_from="bot@example.com",
                notify_email_to="op@example.com",
            )
        )
        assert isinstance(notifier, EmailNotifier)

    def test_both_channels_returns_composite(self):
        from alpaca_bot.notifications.factory import build_notifier

        notifier = build_notifier(
            self._settings(
                slack_webhook_url="https://hooks.slack.com/x",
                notify_smtp_host="smtp.example.com",
                notify_smtp_port=587,
                notify_smtp_user="user",
                notify_smtp_password="pass",
                notify_email_from="bot@example.com",
                notify_email_to="op@example.com",
            )
        )
        assert isinstance(notifier, CompositeNotifier)
