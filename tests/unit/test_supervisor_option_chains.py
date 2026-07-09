from __future__ import annotations

import json
from datetime import date, datetime, timezone
from importlib import import_module
from types import SimpleNamespace

from tests.unit.helpers import _base_env
from alpaca_bot.config import Settings
from alpaca_bot.domain import Bar
from alpaca_bot.domain.models import OptionContract
from alpaca_bot.storage import AuditEvent

_NOW = datetime(2026, 5, 1, 14, 30, tzinfo=timezone.utc)
# Symbols on the watchlist but NOT in settings.symbols ("AAPL,MSFT" from _base_env)
_WATCHLIST_SYMBOLS = ["ACHR", "METC", "SLS"]


class RecordingOptionChainAdapter:
    """Records which symbols were attempted; optionally raises for specific ones."""

    def __init__(
        self,
        *,
        raise_for: set[str] | None = None,
        chains_by_symbol: dict[str, list[OptionContract]] | None = None,
    ) -> None:
        self.fetched: list[str] = []
        self._raise_for = raise_for or set()
        self._chains_by_symbol = chains_by_symbol or {}

    def get_option_chain(self, symbol: str, settings) -> list:
        self.fetched.append(symbol)
        if symbol in self._raise_for:
            raise RuntimeError(f"simulated API error for {symbol}")
        return self._chains_by_symbol.get(symbol, [])


class RecordingAuditStore:
    def __init__(self, events=None) -> None:
        self.events: list = list(events or [])

    def append(self, event, **_) -> None:
        self.events.append(event)

    def load_latest(self, **_): return None
    def list_recent(self, **_): return []
    def list_by_event_types(
        self,
        *,
        event_types,
        limit=20,
        since=None,
        until=None,
        trading_mode=None,
        strategy_version=None,
        **_,
    ):
        rows = [event for event in self.events if event.event_type in event_types]
        if since is not None:
            rows = [event for event in rows if event.created_at >= since]
        if until is not None:
            rows = [event for event in rows if event.created_at < until]
        if trading_mode is not None:
            mode_value = getattr(trading_mode, "value", trading_mode)
            rows = [
                event
                for event in rows
                if event.payload.get("trading_mode") in (None, mode_value)
            ]
        if strategy_version is not None:
            rows = [
                event
                for event in rows
                if event.payload.get("strategy_version") in (None, strategy_version)
            ]
        return list(reversed(rows))[:limit]


def _make_supervisor(*, adapter, audit_store=None, get_stock_bars=None, extra_env=None):
    """Build a RuntimeSupervisor wired with a watchlist returning _WATCHLIST_SYMBOLS."""
    RuntimeSupervisor = import_module("alpaca_bot.runtime.supervisor").RuntimeSupervisor
    env = {
        **_base_env(),
        "ENABLE_OPTIONS_TRADING": "true",
        "OPTION_CHAIN_SYMBOLS": ",".join(_WATCHLIST_SYMBOLS),
        **(extra_env or {}),
    }
    settings = Settings.from_env(env)
    if get_stock_bars is None:
        get_stock_bars = lambda **_: {sym: [] for sym in _WATCHLIST_SYMBOLS}

    class _FakeConn:
        def commit(self): pass
        def rollback(self): pass

    _audit = audit_store or SimpleNamespace(
        append=lambda *a, **k: None,
        load_latest=lambda **_: None,
        list_recent=lambda **_: [],
        list_by_event_types=lambda **_: [],
    )

    runtime = SimpleNamespace(
        connection=_FakeConn(),
        store_lock=None,
        order_store=SimpleNamespace(
            save=lambda *a, **k: None,
            list_by_status=lambda **k: [],
            list_pending_submit=lambda **k: [],
            daily_realized_pnl=lambda **k: 0.0,
            daily_realized_pnl_by_symbol=lambda **k: {},
            list_trade_pnl_by_strategy=lambda **k: [],
        ),
        strategy_weight_store=None,
        trading_status_store=SimpleNamespace(load=lambda **_: None),
        position_store=SimpleNamespace(list_all=lambda **_: [], replace_all=lambda **_: None),
        daily_session_state_store=SimpleNamespace(
            load=lambda **_: None,
            save=lambda state, **_: None,
            list_by_session=lambda **_: [],
        ),
        audit_event_store=_audit,
        strategy_flag_store=None,
        watchlist_store=SimpleNamespace(
            list_enabled=lambda *a: list(_WATCHLIST_SYMBOLS),
            list_ignored=lambda *a: [],
        ),
        option_order_store=None,
    )

    return RuntimeSupervisor(
        settings=settings,
        runtime=runtime,
        broker=SimpleNamespace(
            get_account=lambda: SimpleNamespace(
                equity=10_000.0, buying_power=20_000.0, trading_blocked=False
            ),
            list_open_orders=lambda: [],
            get_open_positions=lambda: [],
            get_clock=lambda: SimpleNamespace(is_open=False),
        ),
        market_data=SimpleNamespace(
            get_stock_bars=get_stock_bars,
            get_daily_bars=lambda **_: {},
        ),
        stream=None,
        close_runtime_fn=lambda _: None,
        connection_checker=lambda _: True,
        cycle_runner=lambda *, strategy_name, **kwargs: SimpleNamespace(intents=[]),
        cycle_intent_executor=lambda **kwargs: SimpleNamespace(
            submitted_exit_count=0, failed_exit_count=0
        ),
        order_dispatcher=lambda **kwargs: {"submitted_count": 0},
        option_chain_adapter=adapter,
    )


def test_option_chain_fetch_uses_option_chain_symbols_not_full_watchlist():
    """Only OPTION_CHAIN_SYMBOLS must be fetched, not every symbol in intraday_bars_by_symbol."""
    # Watchlist has ACHR, METC, SLS but OPTION_CHAIN_SYMBOLS only covers ACHR and METC.
    adapter = RecordingOptionChainAdapter()
    supervisor = _make_supervisor(
        adapter=adapter,
        extra_env={"OPTION_CHAIN_SYMBOLS": "ACHR,METC"},
    )
    supervisor.run_cycle_once(now=lambda: _NOW)

    assert set(adapter.fetched) == {"ACHR", "METC"}, (
        f"Expected only ACHR,METC; got {adapter.fetched!r}"
    )
    assert "SLS" not in adapter.fetched, "SLS not in OPTION_CHAIN_SYMBOLS — must not be fetched"


def test_option_chain_fetch_skipped_when_options_trading_disabled_without_snapshots():
    """Stock-only paper proof must not fetch option chains by default."""
    audit_store = RecordingAuditStore()
    adapter = RecordingOptionChainAdapter()
    supervisor = _make_supervisor(
        adapter=adapter,
        audit_store=audit_store,
        extra_env={"ENABLE_OPTIONS_TRADING": "false"},
    )
    supervisor.run_cycle_once(now=lambda: _NOW)

    assert adapter.fetched == []
    assert not [
        e for e in audit_store.events
        if e.event_type == "option_chains_fetched"
    ]


def test_option_chain_snapshot_records_when_options_trading_disabled(tmp_path):
    """Snapshot-only observation must not require option trading to be enabled."""
    audit_store = RecordingAuditStore()
    contract = OptionContract(
        occ_symbol="ACHR260717C00010000",
        underlying="ACHR",
        option_type="call",
        strike=10.0,
        expiry=date(2026, 7, 17),
        bid=1.2,
        ask=1.35,
        delta=0.52,
        open_interest=240,
    )
    adapter = RecordingOptionChainAdapter(
        chains_by_symbol={"ACHR": [contract]},
    )
    supervisor = _make_supervisor(
        adapter=adapter,
        audit_store=audit_store,
        extra_env={
            "ENABLE_OPTIONS_TRADING": "false",
            "OPTION_CHAIN_SYMBOLS": "ACHR,METC",
            "OPTION_CHAIN_SNAPSHOT_DIR": str(tmp_path),
        },
    )

    supervisor.run_cycle_once(now=lambda: _NOW)

    assert set(adapter.fetched) == {"ACHR", "METC"}
    assert [
        e for e in audit_store.events
        if e.event_type == "option_chains_fetched"
    ]
    assert [
        e for e in audit_store.events
        if e.event_type == "option_chain_snapshot_recorded"
    ]
    assert not [
        e for e in audit_store.events
        if e.event_type == "option_entry_intent_created"
    ]
    assert sorted(tmp_path.glob("option-chain-snapshots-*.jsonl"))


def test_option_chain_snapshot_only_waits_until_due_time(tmp_path):
    """Snapshot-only observation must not delay stock cycles before proof requires it."""
    audit_store = RecordingAuditStore()
    adapter = RecordingOptionChainAdapter()
    supervisor = _make_supervisor(
        adapter=adapter,
        audit_store=audit_store,
        extra_env={
            "ENABLE_OPTIONS_TRADING": "false",
            "OPTION_CHAIN_SYMBOLS": "ACHR,METC",
            "OPTION_CHAIN_SNAPSHOT_DIR": str(tmp_path),
        },
    )
    before_due = datetime(2026, 5, 1, 13, 30, tzinfo=timezone.utc)

    supervisor.run_cycle_once(now=lambda: before_due)

    assert adapter.fetched == []
    assert not [
        e for e in audit_store.events
        if e.event_type.startswith("option_chain_snapshot")
    ]


def test_option_chain_snapshot_only_skips_when_session_snapshot_has_contracts(tmp_path):
    """A positive-contract daily snapshot is enough for replay support."""
    snapshot_path = tmp_path / "option-chain-snapshots-2026-05-01.jsonl"
    snapshot_path.write_text("{}\n", encoding="utf-8")
    audit_store = RecordingAuditStore(
        events=[
            AuditEvent(
                event_type="option_chain_snapshot_recorded",
                payload={
                    "path": str(snapshot_path),
                    "symbols": 2,
                    "contracts": 42,
                    "session_date": "2026-05-01",
                    "trading_mode": "paper",
                    "strategy_version": "v1-breakout",
                },
                created_at=datetime(2026, 5, 1, 14, 5, tzinfo=timezone.utc),
            )
        ]
    )
    adapter = RecordingOptionChainAdapter()
    supervisor = _make_supervisor(
        adapter=adapter,
        audit_store=audit_store,
        extra_env={
            "ENABLE_OPTIONS_TRADING": "false",
            "OPTION_CHAIN_SYMBOLS": "ACHR,METC",
            "OPTION_CHAIN_SNAPSHOT_DIR": str(tmp_path),
        },
    )

    supervisor.run_cycle_once(now=lambda: _NOW)

    assert adapter.fetched == []
    assert not [
        e for e in audit_store.events
        if e.event_type == "option_chains_fetched"
    ]


def test_option_chain_snapshot_only_retries_empty_session_snapshot(tmp_path):
    """An empty snapshot marker must not suppress the due snapshot retry."""
    snapshot_path = tmp_path / "option-chain-snapshots-2026-05-01.jsonl"
    snapshot_path.write_text("{}\n", encoding="utf-8")
    audit_store = RecordingAuditStore(
        events=[
            AuditEvent(
                event_type="option_chain_snapshot_recorded",
                payload={
                    "path": str(snapshot_path),
                    "symbols": 2,
                    "contracts": 0,
                    "session_date": "2026-05-01",
                    "trading_mode": "paper",
                    "strategy_version": "v1-breakout",
                },
                created_at=datetime(2026, 5, 1, 14, 5, tzinfo=timezone.utc),
            )
        ]
    )
    adapter = RecordingOptionChainAdapter()
    supervisor = _make_supervisor(
        adapter=adapter,
        audit_store=audit_store,
        extra_env={
            "ENABLE_OPTIONS_TRADING": "false",
            "OPTION_CHAIN_SYMBOLS": "ACHR,METC",
            "OPTION_CHAIN_SNAPSHOT_DIR": str(tmp_path),
        },
    )

    supervisor.run_cycle_once(now=lambda: _NOW)

    assert set(adapter.fetched) == {"ACHR", "METC"}
    assert [
        e for e in audit_store.events
        if e.event_type == "option_chains_fetched"
    ]


def test_option_chain_fetch_symbol_not_in_bars_is_skipped():
    """A symbol in OPTION_CHAIN_SYMBOLS that has no intraday bars must not be fetched."""
    # Bars only cover ACHR and METC — SLS is configured but absent from bars.
    bars_by_symbol = {
        "ACHR": [Bar(symbol="ACHR", timestamp=_NOW, open=10.0, high=11.0, low=9.0, close=10.5, volume=100_000)],
        "METC": [Bar(symbol="METC", timestamp=_NOW, open=20.0, high=21.0, low=19.0, close=20.5, volume=100_000)],
    }
    adapter = RecordingOptionChainAdapter()
    supervisor = _make_supervisor(
        adapter=adapter,
        get_stock_bars=lambda **_: bars_by_symbol,
        # OPTION_CHAIN_SYMBOLS includes SLS (via _WATCHLIST_SYMBOLS default) but bars don't
    )
    supervisor.run_cycle_once(now=lambda: _NOW)

    assert "SLS" not in adapter.fetched, (
        "SLS has no intraday bars — must not be fetched even though it's in OPTION_CHAIN_SYMBOLS"
    )
    assert "ACHR" in adapter.fetched
    assert "METC" in adapter.fetched


def test_option_chain_empty_symbols_fetches_nothing():
    """With OPTION_CHAIN_SYMBOLS=[], no chains should be fetched."""
    adapter = RecordingOptionChainAdapter()
    supervisor = _make_supervisor(
        adapter=adapter,
        extra_env={"OPTION_CHAIN_SYMBOLS": ""},
    )
    supervisor.run_cycle_once(now=lambda: _NOW)

    assert adapter.fetched == [], (
        f"Expected no fetches with empty OPTION_CHAIN_SYMBOLS; got {adapter.fetched!r}"
    )


def test_option_chain_exception_does_not_block_other_symbols():
    """A fetch exception for one symbol must not prevent other symbols from being attempted."""
    adapter = RecordingOptionChainAdapter(raise_for={"METC"})
    supervisor = _make_supervisor(adapter=adapter)
    supervisor.run_cycle_once(now=lambda: _NOW)  # must not raise

    assert "ACHR" in adapter.fetched
    assert "SLS" in adapter.fetched
    assert "METC" in adapter.fetched  # attempted even though it raised


def test_option_chains_fetched_audit_payload_only_contains_option_chain_symbols():
    """option_chains_fetched payload keys must be exactly OPTION_CHAIN_SYMBOLS, not the full watchlist."""
    audit_store = RecordingAuditStore()
    adapter = RecordingOptionChainAdapter()
    # OPTION_CHAIN_SYMBOLS only covers ACHR and METC — SLS is on the watchlist but not configured
    supervisor = _make_supervisor(
        adapter=adapter,
        audit_store=audit_store,
        extra_env={"OPTION_CHAIN_SYMBOLS": "ACHR,METC"},
    )
    supervisor.run_cycle_once(now=lambda: _NOW)

    chain_events = [e for e in audit_store.events if e.event_type == "option_chains_fetched"]
    assert len(chain_events) == 1, f"Expected 1 option_chains_fetched event, got {len(chain_events)}"
    assert set(chain_events[0].payload) == {"ACHR", "METC"}, (
        f"Audit payload keys {set(chain_events[0].payload)!r} must equal OPTION_CHAIN_SYMBOLS"
    )
    assert "SLS" not in chain_events[0].payload, "SLS is not in OPTION_CHAIN_SYMBOLS — must not appear in payload"


def test_option_chain_snapshot_records_configured_symbol_chains(tmp_path):
    """When configured, option-chain snapshots are written for later replay support."""
    audit_store = RecordingAuditStore()
    contract = OptionContract(
        occ_symbol="ACHR260717C00010000",
        underlying="ACHR",
        option_type="call",
        strike=10.0,
        expiry=date(2026, 7, 17),
        bid=1.2,
        ask=1.35,
        delta=0.52,
        open_interest=240,
    )
    adapter = RecordingOptionChainAdapter(
        chains_by_symbol={"ACHR": [contract]},
    )
    supervisor = _make_supervisor(
        adapter=adapter,
        audit_store=audit_store,
        extra_env={
            "OPTION_CHAIN_SYMBOLS": "ACHR,METC",
            "OPTION_CHAIN_SNAPSHOT_DIR": str(tmp_path),
        },
    )

    supervisor.run_cycle_once(now=lambda: _NOW)

    snapshot_files = sorted(tmp_path.glob("option-chain-snapshots-*.jsonl"))
    assert len(snapshot_files) == 1
    rows = [
        json.loads(line)
        for line in snapshot_files[0].read_text(encoding="utf-8").splitlines()
    ]
    assert rows == [
        {
            "cycle_at": "2026-05-01T14:30:00+00:00",
            "chains_by_symbol": {
                "ACHR": [
                    {
                        "ask": 1.35,
                        "bid": 1.2,
                        "delta": 0.52,
                        "expiry": "2026-07-17",
                        "occ_symbol": "ACHR260717C00010000",
                        "open_interest": 240,
                        "option_type": "call",
                        "strike": 10.0,
                        "underlying": "ACHR",
                    }
                ],
                "METC": [],
            },
        }
    ]
    snapshot_events = [
        e for e in audit_store.events
        if e.event_type == "option_chain_snapshot_recorded"
    ]
    assert len(snapshot_events) == 1
    assert snapshot_events[0].payload["symbols"] == 2
    assert snapshot_events[0].payload["contracts"] == 1


def test_option_chain_snapshot_skipped_when_unconfigured(tmp_path):
    audit_store = RecordingAuditStore()
    adapter = RecordingOptionChainAdapter()
    supervisor = _make_supervisor(adapter=adapter, audit_store=audit_store)

    supervisor.run_cycle_once(now=lambda: _NOW)

    assert list(tmp_path.glob("*")) == []
    assert not [
        e for e in audit_store.events
        if e.event_type.startswith("option_chain_snapshot")
    ]
