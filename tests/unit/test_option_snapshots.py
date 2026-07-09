from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json

import pytest

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal, OptionContract
from alpaca_bot.replay.option_snapshots import (
    OptionChainSnapshot,
    OptionChainSnapshotLedger,
    PointInTimeOptionChains,
    append_option_chain_snapshot,
    freeze_option_chain_snapshots,
    load_option_chain_snapshot_ledger,
    make_point_in_time_option_evaluator,
)
from tests.unit.helpers import _base_env


def _contract(
    occ_symbol: str = "ACHR260717C00010000",
    *,
    ask: float = 1.35,
) -> OptionContract:
    return OptionContract(
        occ_symbol=occ_symbol,
        underlying="ACHR",
        option_type="call",
        strike=10.0,
        expiry=date(2026, 7, 17),
        bid=ask - 0.15,
        ask=ask,
        delta=0.52,
        open_interest=240,
    )


def _settings(**overrides: str) -> Settings:
    env = _base_env()
    env.update({"ENTRY_TIMEFRAME_MINUTES": "5"})
    env.update(overrides)
    return Settings.from_env(env)


def _bar(timestamp: datetime) -> Bar:
    return Bar(
        symbol="ACHR",
        timestamp=timestamp,
        open=10.0,
        high=10.4,
        low=9.8,
        close=10.2,
        volume=1000.0,
    )


def test_append_and_load_option_chain_snapshot_round_trips_contracts(tmp_path):
    cycle_at = datetime(2026, 7, 7, 14, 30, tzinfo=timezone.utc)
    snapshot_path = append_option_chain_snapshot(
        snapshot_dir=tmp_path,
        cycle_at=cycle_at,
        chains_by_symbol={"achr": [_contract()]},
    )

    ledger = load_option_chain_snapshot_ledger(snapshot_path)

    assert len(ledger.snapshots) == 1
    snapshot = ledger.snapshots[0]
    assert snapshot.cycle_at == cycle_at
    assert tuple(snapshot.chains_by_symbol) == ("ACHR",)
    assert snapshot.chains_by_symbol["ACHR"][0] == _contract()


def test_append_option_chain_snapshot_discards_incomplete_jsonl_tail(tmp_path):
    first_at = datetime(2026, 7, 7, 14, 30, tzinfo=timezone.utc)
    snapshot_path = append_option_chain_snapshot(
        snapshot_dir=tmp_path,
        cycle_at=first_at,
        chains_by_symbol={"ACHR": [_contract(ask=1.35)]},
    )
    with snapshot_path.open("ab") as snapshot_file:
        snapshot_file.write(b'{"cycle_at":"2026-07-07T14:')

    second_at = datetime(2026, 7, 7, 14, 45, tzinfo=timezone.utc)
    append_option_chain_snapshot(
        snapshot_dir=tmp_path,
        cycle_at=second_at,
        chains_by_symbol={"ACHR": [_contract(ask=1.55)]},
    )

    raw_lines = snapshot_path.read_bytes().splitlines()
    assert len(raw_lines) == 2
    assert [json.loads(line)["cycle_at"] for line in raw_lines] == [
        first_at.isoformat(),
        second_at.isoformat(),
    ]
    ledger = load_option_chain_snapshot_ledger(snapshot_path)
    assert [snapshot.cycle_at for snapshot in ledger.snapshots] == [
        first_at,
        second_at,
    ]
    assert ledger.snapshots[-1].chains_by_symbol["ACHR"][0].ask == pytest.approx(
        1.55
    )


def test_load_option_chain_snapshot_ledger_filters_by_session_date(tmp_path):
    append_option_chain_snapshot(
        snapshot_dir=tmp_path,
        cycle_at=datetime(2026, 7, 7, 14, 30, tzinfo=timezone.utc),
        chains_by_symbol={"ACHR": [_contract(ask=1.35)]},
    )
    append_option_chain_snapshot(
        snapshot_dir=tmp_path,
        cycle_at=datetime(2026, 7, 8, 14, 30, tzinfo=timezone.utc),
        chains_by_symbol={"ACHR": [_contract(ask=1.50)]},
    )

    ledger = load_option_chain_snapshot_ledger(
        tmp_path,
        session_date=date(2026, 7, 8),
    )

    assert len(ledger.snapshots) == 1
    assert ledger.snapshots[0].cycle_at.date() == date(2026, 7, 8)
    assert ledger.snapshots[0].chains_by_symbol["ACHR"][0].ask == pytest.approx(1.50)


def test_freeze_option_chain_snapshots_keeps_all_sessions_at_replay_boundaries(
    tmp_path,
):
    source_dir = tmp_path / "source"
    for session_offset in range(2):
        session_day = 7 + session_offset
        append_option_chain_snapshot(
            snapshot_dir=source_dir,
            cycle_at=datetime(
                2026, 7, session_day, 14, 30, 30, tzinfo=timezone.utc
            ),
            chains_by_symbol={"ACHR": [_contract(ask=1.35)]},
        )
        append_option_chain_snapshot(
            snapshot_dir=source_dir,
            cycle_at=datetime(
                2026, 7, session_day, 14, 44, tzinfo=timezone.utc
            ),
            chains_by_symbol={"ACHR": [_contract(ask=1.45)]},
        )
        path = append_option_chain_snapshot(
            snapshot_dir=source_dir,
            cycle_at=datetime(
                2026, 7, session_day, 14, 46, tzinfo=timezone.utc
            ),
            chains_by_symbol={"ACHR": [_contract(ask=1.60)]},
        )
        with path.open("a", encoding="utf-8") as snapshot_file:
            snapshot_file.write("{partial")

    summary = freeze_option_chain_snapshots(
        source_dir,
        tmp_path / "frozen",
        interval_minutes=15,
    )

    assert summary.session_count == 2
    assert summary.snapshot_count == 4
    assert summary.min_snapshots_per_session == 2
    assert summary.contract_count == 4
    assert len(tuple(summary.path.glob("option-chain-snapshots-*.jsonl"))) == 2
    ledger = load_option_chain_snapshot_ledger(summary.path)
    assert len(ledger.snapshots) == 4
    for session_day in (7, 8):
        chain = ledger.chain_at_or_before(
            symbol="ACHR",
            as_of=datetime(2026, 7, session_day, 14, 45, tzinfo=timezone.utc),
        )
        assert chain[0].ask == pytest.approx(1.45)


def test_ledger_returns_latest_chain_at_or_before_timestamp():
    first = OptionChainSnapshot(
        cycle_at=datetime(2026, 7, 7, 14, 30, tzinfo=timezone.utc),
        chains_by_symbol={"ACHR": (_contract(ask=1.35),)},
    )
    second = OptionChainSnapshot(
        cycle_at=datetime(2026, 7, 7, 14, 45, tzinfo=timezone.utc),
        chains_by_symbol={"ACHR": (_contract(ask=1.55),)},
    )
    ledger = OptionChainSnapshotLedger((second, first))

    before = ledger.chain_at_or_before(
        symbol="ACHR",
        as_of=first.cycle_at - timedelta(seconds=1),
    )
    between = ledger.chain_at_or_before(
        symbol="achr",
        as_of=datetime(2026, 7, 7, 14, 40, tzinfo=timezone.utc),
    )
    after = ledger.chain_at_or_before(
        symbol="ACHR",
        as_of=datetime(2026, 7, 7, 14, 50, tzinfo=timezone.utc),
    )

    assert before == ()
    assert between[0].ask == pytest.approx(1.35)
    assert after[0].ask == pytest.approx(1.55)


def test_ledger_returns_snapshot_symbols_at_or_before_timestamp():
    first = OptionChainSnapshot(
        cycle_at=datetime(2026, 7, 7, 14, 30, tzinfo=timezone.utc),
        chains_by_symbol={"ACHR": (_contract(ask=1.35),)},
    )
    second = OptionChainSnapshot(
        cycle_at=datetime(2026, 7, 7, 14, 45, tzinfo=timezone.utc),
        chains_by_symbol={"ACHR": (_contract(ask=1.55),), "METC": ()},
    )
    ledger = OptionChainSnapshotLedger((first, second))

    assert ledger.symbols_at_or_before(
        as_of=datetime(2026, 7, 7, 14, 29, tzinfo=timezone.utc),
    ) == ()
    assert ledger.symbols_at_or_before(
        as_of=datetime(2026, 7, 7, 14, 40, tzinfo=timezone.utc),
    ) == ("ACHR",)
    assert ledger.symbols_at_or_before(
        as_of=datetime(2026, 7, 7, 14, 50, tzinfo=timezone.utc),
    ) == ("ACHR", "METC")


def test_ledger_returns_latest_contract_mark_at_or_before_timestamp():
    first = OptionChainSnapshot(
        cycle_at=datetime(2026, 7, 7, 14, 30, tzinfo=timezone.utc),
        chains_by_symbol={"ACHR": (_contract(ask=1.35),)},
    )
    second = OptionChainSnapshot(
        cycle_at=datetime(2026, 7, 7, 14, 45, tzinfo=timezone.utc),
        chains_by_symbol={"ACHR": (_contract(ask=1.55),)},
    )
    ledger = OptionChainSnapshotLedger((first, second))

    contract = ledger.contract_at_or_before(
        occ_symbol="achr260717c00010000",
        as_of=datetime(2026, 7, 7, 14, 50, tzinfo=timezone.utc),
    )

    assert contract is not None
    assert contract.ask == pytest.approx(1.55)
    assert ledger.contract_at_or_before(
        occ_symbol="MISSING260717C00010000",
        as_of=datetime(2026, 7, 7, 14, 50, tzinfo=timezone.utc),
    ) is None


def test_ledger_does_not_carry_snapshots_across_utc_sessions():
    ledger = OptionChainSnapshotLedger(
        (
            OptionChainSnapshot(
                cycle_at=datetime(2026, 7, 7, 14, 30, tzinfo=timezone.utc),
                chains_by_symbol={"ACHR": (_contract(ask=1.35),)},
            ),
        )
    )
    next_session = datetime(2026, 7, 8, 14, 30, tzinfo=timezone.utc)

    assert ledger.snapshot_at_or_before(as_of=next_session) is None
    assert ledger.symbols_at_or_before(as_of=next_session) == ()
    assert ledger.chain_at_or_before(symbol="ACHR", as_of=next_session) == ()
    assert ledger.contract_at_or_before(
        occ_symbol="ACHR260717C00010000",
        as_of=next_session,
    ) is None


def test_point_in_time_option_chains_requires_as_of_before_use():
    mapping = PointInTimeOptionChains(
        OptionChainSnapshotLedger(
            (
                OptionChainSnapshot(
                    cycle_at=datetime(2026, 7, 7, 14, 30, tzinfo=timezone.utc),
                    chains_by_symbol={"ACHR": (_contract(),)},
                ),
            )
        )
    )

    with pytest.raises(RuntimeError, match="as_of must be set"):
        mapping.get("ACHR", ())


def test_point_in_time_option_chains_matches_factory_mapping_contract():
    first = OptionChainSnapshot(
        cycle_at=datetime(2026, 7, 7, 14, 30, tzinfo=timezone.utc),
        chains_by_symbol={"ACHR": (_contract(ask=1.35),)},
    )
    second = OptionChainSnapshot(
        cycle_at=datetime(2026, 7, 7, 14, 45, tzinfo=timezone.utc),
        chains_by_symbol={"ACHR": (_contract(ask=1.55),), "METC": ()},
    )
    mapping = PointInTimeOptionChains(OptionChainSnapshotLedger((first, second)))

    mapping.set_as_of(datetime(2026, 7, 7, 14, 40, tzinfo=timezone.utc))
    assert list(mapping) == ["ACHR"]
    assert len(mapping) == 1
    assert mapping.get("achr", ())[0].ask == pytest.approx(1.35)
    assert mapping.get("METC", ()) == ()

    mapping.set_as_of(datetime(2026, 7, 7, 14, 50, tzinfo=timezone.utc))
    assert list(mapping) == ["ACHR", "METC"]
    assert len(mapping) == 2
    assert mapping.get("ACHR", ())[0].ask == pytest.approx(1.55)
    assert mapping.get("METC", ()) == ()


def test_point_in_time_option_evaluator_uses_chain_available_at_evaluation_time():
    ledger = OptionChainSnapshotLedger(
        (
            OptionChainSnapshot(
                cycle_at=datetime(2026, 7, 7, 14, 30, tzinfo=timezone.utc),
                chains_by_symbol={"ACHR": (_contract(ask=1.35),)},
            ),
            OptionChainSnapshot(
                cycle_at=datetime(2026, 7, 7, 14, 35, tzinfo=timezone.utc),
                chains_by_symbol={"ACHR": (_contract(ask=1.55),)},
            ),
            OptionChainSnapshot(
                cycle_at=datetime(2026, 7, 7, 14, 36, tzinfo=timezone.utc),
                chains_by_symbol={"ACHR": (_contract(ask=9.99),)},
            ),
        )
    )

    def factory(chains):
        def evaluate(
            *,
            symbol,
            intraday_bars,
            signal_index,
            daily_bars,
            settings,
        ):
            del daily_bars, settings
            contracts = chains.get(symbol, ())
            if not contracts:
                return None
            contract = contracts[0]
            bar = intraday_bars[signal_index]
            return EntrySignal(
                symbol=symbol,
                signal_bar=bar,
                entry_level=bar.close,
                relative_volume=1.0,
                stop_price=0.0,
                limit_price=contract.ask,
                initial_stop_price=0.01,
                option_contract=contract,
            )

        return evaluate

    evaluator = make_point_in_time_option_evaluator(factory, ledger)
    bars = [_bar(datetime(2026, 7, 7, 14, 30, tzinfo=timezone.utc))]

    signal = evaluator(
        symbol="ACHR",
        intraday_bars=bars,
        signal_index=0,
        daily_bars=[],
        settings=_settings(),
    )

    assert signal is not None
    assert signal.option_contract is not None
    assert signal.option_contract.ask == pytest.approx(1.55)
    assert signal.limit_price == pytest.approx(1.55)


def test_point_in_time_option_evaluator_returns_none_for_invalid_signal_index():
    ledger = OptionChainSnapshotLedger(
        (
            OptionChainSnapshot(
                cycle_at=datetime(2026, 7, 7, 14, 30, tzinfo=timezone.utc),
                chains_by_symbol={"ACHR": (_contract(),)},
            ),
        )
    )

    def factory(chains):
        del chains

        def evaluate(**kwargs):
            raise AssertionError("invalid signal indexes must not call evaluator")

        return evaluate

    evaluator = make_point_in_time_option_evaluator(factory, ledger)

    assert (
        evaluator(
            symbol="ACHR",
            intraday_bars=[],
            signal_index=0,
            daily_bars=[],
            settings=_settings(),
        )
        is None
    )


def test_load_option_chain_snapshot_reports_file_and_line_for_bad_rows(tmp_path):
    path = tmp_path / "option-chain-snapshots-2026-07-07.jsonl"
    path.write_text(
        json.dumps({"cycle_at": "2026-07-07T14:30:00+00:00"}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"option-chain-snapshots-2026-07-07.jsonl:1"):
        load_option_chain_snapshot_ledger(path)


def test_load_option_chain_snapshot_rejects_cycle_date_file_mismatch(tmp_path):
    path = tmp_path / "option-chain-snapshots-2026-07-08.jsonl"
    path.write_text(
        json.dumps(
            {
                "cycle_at": "2026-07-07T14:30:00+00:00",
                "chains_by_symbol": {
                    "ACHR": [
                        {
                            "ask": 1.35,
                            "bid": 1.2,
                            "expiry": "2026-07-17",
                            "occ_symbol": "ACHR260717C00010000",
                            "option_type": "call",
                            "strike": 10.0,
                            "underlying": "ACHR",
                        }
                    ]
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"option-chain-snapshots-2026-07-08.jsonl:1"):
        load_option_chain_snapshot_ledger(path)
