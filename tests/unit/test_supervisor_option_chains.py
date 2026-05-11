from __future__ import annotations

from datetime import datetime, timezone
from importlib import import_module
from types import SimpleNamespace

from tests.unit.helpers import _base_env
from alpaca_bot.config import Settings
from alpaca_bot.domain import Bar

_NOW = datetime(2026, 5, 1, 14, 30, tzinfo=timezone.utc)
# Symbols on the watchlist but NOT in settings.symbols ("AAPL,MSFT" from _base_env)
_WATCHLIST_SYMBOLS = ["ACHR", "METC", "SLS"]


class RecordingOptionChainAdapter:
    """Records which symbols were attempted; optionally raises for specific ones."""

    def __init__(self, *, raise_for: set[str] | None = None) -> None:
        self.fetched: list[str] = []
        self._raise_for = raise_for or set()

    def get_option_chain(self, symbol: str, settings) -> list:
        self.fetched.append(symbol)
        if symbol in self._raise_for:
            raise RuntimeError(f"simulated API error for {symbol}")
        return []


class RecordingAuditStore:
    def __init__(self) -> None:
        self.events: list = []

    def append(self, event, **_) -> None:
        self.events.append(event)

    def load_latest(self, **_): return None
    def list_recent(self, **_): return []
    def list_by_event_types(self, **_): return []


def _make_supervisor(*, adapter, audit_store=None, get_stock_bars=None, extra_env=None):
    """Build a RuntimeSupervisor wired with a watchlist returning _WATCHLIST_SYMBOLS."""
    RuntimeSupervisor = import_module("alpaca_bot.runtime.supervisor").RuntimeSupervisor
    env = {**_base_env(), "ENABLE_OPTIONS_TRADING": "true", **(extra_env or {})}
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


def test_option_chain_fetch_uses_watchlist_not_settings_symbols():
    """Adapter must be called for intraday_bars_by_symbol keys, not settings.symbols."""
    adapter = RecordingOptionChainAdapter()
    supervisor = _make_supervisor(adapter=adapter)
    supervisor.run_cycle_once(now=lambda: _NOW)

    assert set(adapter.fetched) == set(_WATCHLIST_SYMBOLS), (
        f"Expected fetches for {_WATCHLIST_SYMBOLS!r}, got {adapter.fetched!r}"
    )
    for sym in ("AAPL", "MSFT"):
        assert sym not in adapter.fetched, (
            f"{sym!r} is in settings.symbols but not the watchlist — must not be fetched"
        )


def test_option_chain_exception_does_not_block_other_symbols():
    """A fetch exception for one symbol must not prevent other symbols from being attempted."""
    adapter = RecordingOptionChainAdapter(raise_for={"METC"})
    supervisor = _make_supervisor(adapter=adapter)
    supervisor.run_cycle_once(now=lambda: _NOW)  # must not raise

    assert "ACHR" in adapter.fetched
    assert "SLS" in adapter.fetched
    assert "METC" in adapter.fetched  # attempted even though it raised


def test_option_chains_fetched_audit_event_keys_match_watchlist():
    """option_chains_fetched payload keys must equal intraday_bars_by_symbol keys."""
    audit_store = RecordingAuditStore()
    adapter = RecordingOptionChainAdapter()
    supervisor = _make_supervisor(adapter=adapter, audit_store=audit_store)
    supervisor.run_cycle_once(now=lambda: _NOW)

    chain_events = [e for e in audit_store.events if e.event_type == "option_chains_fetched"]
    assert len(chain_events) == 1, f"Expected 1 option_chains_fetched event, got {len(chain_events)}"
    assert set(chain_events[0].payload) == set(_WATCHLIST_SYMBOLS), (
        f"Audit payload keys {set(chain_events[0].payload)!r} must equal "
        f"watchlist {set(_WATCHLIST_SYMBOLS)!r}"
    )


def test_volume_filter_excludes_low_volume_symbols() -> None:
    """Symbols below OPTION_CHAIN_MIN_TOTAL_VOLUME must not reach the adapter."""
    # ACHR and METC each have 100,000 volume (>= threshold=50,000) → fetched.
    # SLS has 10,000 volume (< threshold) → must NOT be fetched.
    bars_by_symbol = {
        "ACHR": [Bar(symbol="ACHR", timestamp=_NOW, open=10.0, high=11.0, low=9.0, close=10.5, volume=100_000)],
        "METC": [Bar(symbol="METC", timestamp=_NOW, open=20.0, high=21.0, low=19.0, close=20.5, volume=100_000)],
        "SLS":  [Bar(symbol="SLS",  timestamp=_NOW, open=5.0,  high=6.0,  low=4.5,  close=5.5,  volume=10_000)],
    }
    adapter = RecordingOptionChainAdapter()
    supervisor = _make_supervisor(
        adapter=adapter,
        get_stock_bars=lambda **_: bars_by_symbol,
        extra_env={"OPTION_CHAIN_MIN_TOTAL_VOLUME": "50000"},
    )
    supervisor.run_cycle_once(now=lambda: _NOW)

    assert "ACHR" in adapter.fetched, "ACHR (volume=100k) must be fetched"
    assert "METC" in adapter.fetched, "METC (volume=100k) must be fetched"
    assert "SLS" not in adapter.fetched, (
        f"SLS (volume=10k < threshold=50k) must not be fetched; got {adapter.fetched!r}"
    )


def test_volume_filter_zero_disables_filter() -> None:
    """OPTION_CHAIN_MIN_TOTAL_VOLUME=0 must fetch all symbols regardless of volume."""
    # SLS has only 1 share of volume — far below any practical threshold.
    # With min_vol=0, the filter is disabled and SLS must still be fetched.
    bars_by_symbol = {
        "ACHR": [Bar(symbol="ACHR", timestamp=_NOW, open=10.0, high=11.0, low=9.0, close=10.5, volume=100_000)],
        "METC": [Bar(symbol="METC", timestamp=_NOW, open=20.0, high=21.0, low=19.0, close=20.5, volume=100_000)],
        "SLS":  [Bar(symbol="SLS",  timestamp=_NOW, open=5.0,  high=6.0,  low=4.5,  close=5.5,  volume=1)],
    }
    adapter = RecordingOptionChainAdapter()
    supervisor = _make_supervisor(
        adapter=adapter,
        get_stock_bars=lambda **_: bars_by_symbol,
        # extra_env omitted → OPTION_CHAIN_MIN_TOTAL_VOLUME defaults to "0"
    )
    supervisor.run_cycle_once(now=lambda: _NOW)

    assert set(adapter.fetched) == {"ACHR", "METC", "SLS"}, (
        f"All symbols must be fetched when min_vol=0; got {adapter.fetched!r}"
    )
