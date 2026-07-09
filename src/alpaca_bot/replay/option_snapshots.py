from __future__ import annotations

import argparse
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Any

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal, OptionContract
from alpaca_bot.strategy import StrategySignalEvaluator


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
            if snapshot.cycle_at.date() != as_of_utc.date():
                continue
            if snapshot.cycle_at <= as_of_utc:
                return snapshot.chains_by_symbol.get(symbol_key, ())
        return ()

    def symbols_at_or_before(self, *, as_of: datetime) -> tuple[str, ...]:
        snapshot = self.snapshot_at_or_before(as_of=as_of)
        if snapshot is None:
            return ()
        return tuple(snapshot.chains_by_symbol)

    def contract_at_or_before(
        self,
        *,
        occ_symbol: str,
        as_of: datetime,
    ) -> OptionContract | None:
        occ_key = occ_symbol.upper()
        as_of_utc = _normalize_utc(as_of)
        for snapshot in reversed(self.snapshots):
            if snapshot.cycle_at.date() != as_of_utc.date():
                continue
            if snapshot.cycle_at > as_of_utc:
                continue
            for contracts in snapshot.chains_by_symbol.values():
                for contract in contracts:
                    if contract.occ_symbol.upper() == occ_key:
                        return contract
        return None

    def snapshot_at_or_before(self, *, as_of: datetime) -> OptionChainSnapshot | None:
        as_of_utc = _normalize_utc(as_of)
        for snapshot in reversed(self.snapshots):
            if snapshot.cycle_at.date() != as_of_utc.date():
                continue
            if snapshot.cycle_at <= as_of_utc:
                return snapshot
        return None


@dataclass(frozen=True)
class FrozenOptionSnapshotSummary:
    path: Path
    contract_count: int
    session_count: int
    snapshot_count: int


@dataclass
class PointInTimeOptionChains(Mapping[str, tuple[OptionContract, ...]]):
    ledger: OptionChainSnapshotLedger
    as_of: datetime | None = None

    def set_as_of(self, as_of: datetime) -> None:
        self.as_of = _normalize_utc(as_of)

    def __getitem__(self, symbol: str) -> tuple[OptionContract, ...]:
        as_of = self._require_as_of()
        symbol_key = symbol.upper()
        if symbol_key not in self.ledger.symbols_at_or_before(as_of=as_of):
            raise KeyError(symbol)
        return self.ledger.chain_at_or_before(symbol=symbol_key, as_of=as_of)

    def __iter__(self) -> Iterator[str]:
        return iter(self.ledger.symbols_at_or_before(as_of=self._require_as_of()))

    def __len__(self) -> int:
        return len(self.ledger.symbols_at_or_before(as_of=self._require_as_of()))

    def _require_as_of(self) -> datetime:
        if self.as_of is None:
            raise RuntimeError("PointInTimeOptionChains.as_of must be set before use")
        return self.as_of


@dataclass
class PointInTimeOptionSignalEvaluator:
    evaluator: StrategySignalEvaluator
    chains: PointInTimeOptionChains

    def __call__(
        self,
        *,
        symbol: str,
        intraday_bars: Sequence[Bar],
        signal_index: int,
        daily_bars: Sequence[Bar],
        settings: Settings,
    ) -> EntrySignal | None:
        if signal_index < 0 or signal_index >= len(intraday_bars):
            return None
        signal_bar = intraday_bars[signal_index]
        self.chains.set_as_of(
            signal_bar.timestamp
            + timedelta(minutes=settings.entry_timeframe_minutes)
        )
        return self.evaluator(
            symbol=symbol,
            intraday_bars=intraday_bars,
            signal_index=signal_index,
            daily_bars=daily_bars,
            settings=settings,
        )


def make_point_in_time_option_evaluator(
    factory: Callable[[Mapping[str, Sequence[OptionContract]]], StrategySignalEvaluator],
    ledger: OptionChainSnapshotLedger,
) -> PointInTimeOptionSignalEvaluator:
    chains = PointInTimeOptionChains(ledger)
    return PointInTimeOptionSignalEvaluator(factory(chains), chains)


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
    record = (
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    with path.open("a+b") as handle:
        _truncate_incomplete_jsonl_tail(handle)
        handle.write(record)
    return path


def _truncate_incomplete_jsonl_tail(handle: Any) -> None:
    handle.seek(0, os.SEEK_END)
    size = handle.tell()
    if size == 0:
        return
    handle.seek(-1, os.SEEK_END)
    if handle.read(1) == b"\n":
        return

    cursor = size
    while cursor > 0:
        chunk_start = max(0, cursor - 8192)
        handle.seek(chunk_start)
        chunk = handle.read(cursor - chunk_start)
        newline_at = chunk.rfind(b"\n")
        if newline_at >= 0:
            handle.truncate(chunk_start + newline_at + 1)
            return
        cursor = chunk_start
    handle.truncate(0)


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


def freeze_option_chain_snapshots(
    source_path: str | Path,
    destination_root: str | Path,
    *,
    interval_minutes: int = 15,
) -> FrozenOptionSnapshotSummary:
    """Freeze all sessions, retaining the last mark before each replay boundary."""
    if interval_minutes <= 0:
        raise ValueError("interval_minutes must be positive")
    source = Path(source_path)
    destination = Path(destination_root)
    try:
        files = [
            path
            for path in _snapshot_files(source, session_date=None)
            if path.is_file() and path.stat().st_size > 0
        ]
    except OSError as exc:
        raise ValueError(f"could not inspect option-chain snapshots: {source}") from exc
    if not files:
        raise ValueError("no replayable option-chain snapshot file found")

    destination.mkdir(parents=True, exist_ok=True)
    expected_names = {path.name for path in files}
    for stale_path in destination.glob("option-chain-snapshots-*.jsonl"):
        if stale_path.name not in expected_names:
            stale_path.unlink()

    interval_microseconds = interval_minutes * 60 * 1_000_000
    total_contracts = 0
    total_sessions = 0
    total_snapshots = 0
    for file_path in files:
        file_session = _snapshot_file_session(file_path)
        if file_session is None:
            raise ValueError(f"invalid option snapshot filename: {file_path}")
        retained: dict[int, tuple[datetime, str, int]] = {}
        with file_path.open(encoding="utf-8") as source_file:
            for raw_line in source_file:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    # The source may be actively appending its final line.
                    break
                try:
                    cycle_at = _normalize_utc(
                        datetime.fromisoformat(str(payload["cycle_at"]))
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError(
                        f"invalid option snapshot cycle_at: {file_path}"
                    ) from exc
                if cycle_at.date() != file_session:
                    raise ValueError(
                        "option-chain snapshot cycle_at date does not match filename"
                    )
                chains_by_symbol = payload.get("chains_by_symbol")
                if not isinstance(chains_by_symbol, dict):
                    raise ValueError("option-chain snapshot missing chains_by_symbol")
                contract_count = 0
                for contracts in chains_by_symbol.values():
                    if not isinstance(contracts, list):
                        raise ValueError(
                            "option-chain snapshot contracts must be lists"
                        )
                    contract_count += len(contracts)
                epoch_microseconds = (
                    int(cycle_at.timestamp()) * 1_000_000 + cycle_at.microsecond
                )
                boundary = (
                    epoch_microseconds + interval_microseconds - 1
                ) // interval_microseconds
                current = retained.get(boundary)
                if current is None or cycle_at > current[0]:
                    retained[boundary] = (cycle_at, line, contract_count)

        ordered = sorted(retained.values(), key=lambda item: item[0])
        if not ordered or sum(item[2] for item in ordered) <= 0:
            continue
        destination_path = destination / file_path.name
        temporary_path = destination_path.with_name(
            f"{destination_path.name}.tmp.{os.getpid()}"
        )
        temporary_path.write_text("".join(f"{item[1]}\n" for item in ordered))
        temporary_path.replace(destination_path)
        total_contracts += sum(item[2] for item in ordered)
        total_sessions += 1
        total_snapshots += len(ordered)

    if total_sessions <= 0 or total_contracts <= 0:
        raise ValueError("frozen option-chain snapshots have no replayable contracts")
    return FrozenOptionSnapshotSummary(
        path=destination,
        contract_count=total_contracts,
        session_count=total_sessions,
        snapshot_count=total_snapshots,
    )


def _snapshot_files(path: Path, *, session_date: date | None) -> list[Path]:
    if path.is_file():
        return [path]
    if session_date is not None:
        candidate = path / f"option-chain-snapshots-{session_date.isoformat()}.jsonl"
        return [candidate] if candidate.exists() else []
    return sorted(path.glob("option-chain-snapshots-*.jsonl"))


def _snapshot_file_session(path: Path) -> date | None:
    stem = path.stem
    prefix = "option-chain-snapshots-"
    if not stem.startswith(prefix):
        return None
    try:
        return date.fromisoformat(stem.removeprefix(prefix))
    except ValueError:
        return None


def _load_snapshot_file(path: Path) -> list[OptionChainSnapshot]:
    snapshots: list[OptionChainSnapshot] = []
    file_session = _snapshot_file_session(path)
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                snapshot = _snapshot_from_payload(payload)
                if (
                    file_session is not None
                    and snapshot.cycle_at.date() != file_session
                ):
                    raise ValueError(
                        "cycle_at date does not match snapshot file session"
                    )
                snapshots.append(snapshot)
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Option-chain snapshot utilities.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    freeze_parser = subparsers.add_parser("freeze")
    freeze_parser.add_argument("source", type=Path)
    freeze_parser.add_argument("destination", type=Path)
    freeze_parser.add_argument("--interval-minutes", type=int, default=15)
    args = parser.parse_args(argv)
    if args.command == "freeze":
        try:
            summary = freeze_option_chain_snapshots(
                args.source,
                args.destination,
                interval_minutes=args.interval_minutes,
            )
        except ValueError as exc:
            parser.error(str(exc))
        print(
            f"{summary.path}\t{summary.contract_count}\t"
            f"{summary.session_count}\t{summary.snapshot_count}"
        )
        return 0
    parser.error(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
