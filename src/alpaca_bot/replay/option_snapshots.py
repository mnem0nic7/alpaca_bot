from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from alpaca_bot.domain.models import OptionContract


@dataclass(frozen=True)
class OptionChainSnapshot:
    cycle_at: datetime
    chains_by_symbol: Mapping[str, tuple[OptionContract, ...]]


@dataclass(frozen=True)
class OptionChainSnapshotLedger:
    snapshots: tuple[OptionChainSnapshot, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "snapshots",
            tuple(sorted(self.snapshots, key=lambda snapshot: snapshot.cycle_at)),
        )

    def chain_at_or_before(
        self,
        *,
        symbol: str,
        as_of: datetime,
    ) -> tuple[OptionContract, ...]:
        symbol_key = symbol.upper()
        as_of_utc = _normalize_utc(as_of)
        for snapshot in reversed(self.snapshots):
            if snapshot.cycle_at <= as_of_utc:
                return snapshot.chains_by_symbol.get(symbol_key, ())
        return ()

    def contract_at_or_before(
        self,
        *,
        occ_symbol: str,
        as_of: datetime,
    ) -> OptionContract | None:
        occ_key = occ_symbol.upper()
        as_of_utc = _normalize_utc(as_of)
        for snapshot in reversed(self.snapshots):
            if snapshot.cycle_at > as_of_utc:
                continue
            for contracts in snapshot.chains_by_symbol.values():
                for contract in contracts:
                    if contract.occ_symbol.upper() == occ_key:
                        return contract
        return None


def append_option_chain_snapshot(
    *,
    snapshot_dir: str | Path,
    cycle_at: datetime,
    chains_by_symbol: Mapping[str, Sequence[OptionContract]],
) -> Path:
    """Append one replay-grade option-chain snapshot to a UTC-dated JSONL file."""
    root = Path(snapshot_dir)
    root.mkdir(parents=True, exist_ok=True)
    cycle_utc = _normalize_utc(cycle_at)
    path = root / f"option-chain-snapshots-{cycle_utc.date().isoformat()}.jsonl"
    payload = {
        "cycle_at": cycle_utc.isoformat(),
        "chains_by_symbol": {
            symbol.upper(): [_contract_payload(contract) for contract in contracts]
            for symbol, contracts in sorted(chains_by_symbol.items())
        },
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        handle.write("\n")
    return path


def load_option_chain_snapshot_ledger(
    snapshot_path: str | Path,
    *,
    session_date: date | None = None,
) -> OptionChainSnapshotLedger:
    path = Path(snapshot_path)
    files = tuple(_snapshot_files(path, session_date=session_date))
    snapshots: list[OptionChainSnapshot] = []
    for file_path in files:
        snapshots.extend(_load_snapshot_file(file_path))
    return OptionChainSnapshotLedger(tuple(snapshots))


def _snapshot_files(path: Path, *, session_date: date | None) -> list[Path]:
    if path.is_file():
        return [path]
    if session_date is not None:
        candidate = path / f"option-chain-snapshots-{session_date.isoformat()}.jsonl"
        return [candidate] if candidate.exists() else []
    return sorted(path.glob("option-chain-snapshots-*.jsonl"))


def _load_snapshot_file(path: Path) -> list[OptionChainSnapshot]:
    snapshots: list[OptionChainSnapshot] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                snapshots.append(_snapshot_from_payload(payload))
            except Exception as exc:
                raise ValueError(
                    f"invalid option-chain snapshot {path}:{line_number}"
                ) from exc
    return snapshots


def _snapshot_from_payload(payload: Mapping[str, Any]) -> OptionChainSnapshot:
    cycle_at = _normalize_utc(datetime.fromisoformat(str(payload["cycle_at"])))
    raw_chains = payload.get("chains_by_symbol")
    if not isinstance(raw_chains, Mapping):
        raise ValueError("chains_by_symbol must be an object")
    chains_by_symbol: dict[str, tuple[OptionContract, ...]] = {}
    for symbol, raw_contracts in raw_chains.items():
        if not isinstance(raw_contracts, Sequence) or isinstance(raw_contracts, str):
            raise ValueError(f"contracts for {symbol!r} must be a list")
        chains_by_symbol[str(symbol).upper()] = tuple(
            _contract_from_payload(contract_payload)
            for contract_payload in raw_contracts
        )
    return OptionChainSnapshot(
        cycle_at=cycle_at,
        chains_by_symbol=chains_by_symbol,
    )


def _contract_payload(contract: OptionContract) -> dict[str, object]:
    return {
        "occ_symbol": contract.occ_symbol,
        "underlying": contract.underlying,
        "option_type": contract.option_type,
        "strike": contract.strike,
        "expiry": contract.expiry.isoformat(),
        "bid": contract.bid,
        "ask": contract.ask,
        "delta": contract.delta,
        "open_interest": contract.open_interest,
    }


def _contract_from_payload(payload: Any) -> OptionContract:
    if not isinstance(payload, Mapping):
        raise ValueError("contract payload must be an object")
    return OptionContract(
        occ_symbol=str(payload["occ_symbol"]).upper(),
        underlying=str(payload["underlying"]).upper(),
        option_type=str(payload["option_type"]),
        strike=float(payload["strike"]),
        expiry=date.fromisoformat(str(payload["expiry"])),
        bid=float(payload["bid"]),
        ask=float(payload["ask"]),
        delta=(
            None
            if payload.get("delta") is None
            else float(payload["delta"])
        ),
        open_interest=(
            None
            if payload.get("open_interest") is None
            else int(payload["open_interest"])
        ),
    )


def _normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
