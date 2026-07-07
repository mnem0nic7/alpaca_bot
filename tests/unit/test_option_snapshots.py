from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json

import pytest

from alpaca_bot.domain.models import OptionContract
from alpaca_bot.replay.option_snapshots import (
    OptionChainSnapshot,
    OptionChainSnapshotLedger,
    append_option_chain_snapshot,
    load_option_chain_snapshot_ledger,
)


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


def test_load_option_chain_snapshot_reports_file_and_line_for_bad_rows(tmp_path):
    path = tmp_path / "option-chain-snapshots-2026-07-07.jsonl"
    path.write_text(
        json.dumps({"cycle_at": "2026-07-07T14:30:00+00:00"}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"option-chain-snapshots-2026-07-07.jsonl:1"):
        load_option_chain_snapshot_ledger(path)
