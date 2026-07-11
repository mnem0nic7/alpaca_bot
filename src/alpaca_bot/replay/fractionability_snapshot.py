from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from alpaca_bot.config import Settings
from alpaca_bot.execution.alpaca import AlpacaExecutionAdapter
from alpaca_bot.storage.db import connect_postgres
from alpaca_bot.storage.repositories import WatchlistStore


_SYMBOL_PATTERN = re.compile(r"[A-Z0-9][A-Z0-9.-]*")


@dataclass(frozen=True)
class LoadedFractionabilitySnapshot:
    path: Path
    symbols: frozenset[str]
    sha256: str


def load_fractionability_snapshot(path: Path) -> LoadedFractionabilitySnapshot:
    try:
        raw_bytes = path.read_bytes()
        text = raw_bytes.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"could not read fractionability snapshot {path}: {exc}") from exc

    symbols: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.partition("#")[0]
        for raw_symbol in line.replace(",", " ").split():
            symbol = raw_symbol.strip().upper()
            if _SYMBOL_PATTERN.fullmatch(symbol) is None:
                raise ValueError(
                    f"fractionability snapshot contains invalid symbol: {raw_symbol}"
                )
            symbols.add(symbol)

    return LoadedFractionabilitySnapshot(
        path=path.resolve(),
        symbols=frozenset(symbols),
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
    )


def scenario_symbols(
    scenario_dir: Path,
    *,
    allowed_symbols: Iterable[str] | None = None,
) -> tuple[str, ...]:
    try:
        paths = sorted(scenario_dir.glob("*.json"))
    except OSError as exc:
        raise ValueError(f"could not inspect scenario directory {scenario_dir}: {exc}") from exc
    if not paths:
        raise ValueError(f"scenario directory contains no JSON scenarios: {scenario_dir}")

    symbols_by_name: dict[str, Path] = {}
    for path in paths:
        if not path.is_file():
            raise ValueError(f"scenario path is not a readable file: {path}")
        stem = path.stem
        if stem.endswith("_252d"):
            stem = stem[:-5]
        symbol = stem.upper()
        if _SYMBOL_PATTERN.fullmatch(symbol) is None:
            raise ValueError(f"could not derive a valid symbol from scenario: {path}")
        previous = symbols_by_name.get(symbol)
        if previous is not None:
            raise ValueError(
                f"duplicate scenario symbol {symbol}: {previous} and {path}"
            )
        symbols_by_name[symbol] = path
    if allowed_symbols is None:
        return tuple(sorted(symbols_by_name))

    selected: set[str] = set()
    for raw_symbol in allowed_symbols:
        symbol = str(raw_symbol).upper()
        if _SYMBOL_PATTERN.fullmatch(symbol) is None:
            raise ValueError(f"active universe contains invalid symbol: {raw_symbol}")
        selected.add(symbol)
    if not selected:
        raise ValueError("active scenario universe is empty")
    missing = sorted(selected - symbols_by_name.keys())
    if missing:
        preview = ",".join(missing[:20])
        raise ValueError(
            f"scenario directory is missing {len(missing)} active symbol(s): {preview}"
        )
    return tuple(sorted(selected))


def capture_fractionability_snapshot(
    *,
    scenario_dir: Path,
    output_path: Path,
    metadata_path: Path,
    universe_output_path: Path | None = None,
    universe_symbols: Iterable[str] | None = None,
    source_path: Path | None = None,
    expected_source_sha256: str | None = None,
    expected_universe_sha256: str | None = None,
    resolve_fractionable: Callable[[Sequence[str]], Iterable[str]] | None = None,
) -> dict[str, object]:
    universe = scenario_symbols(
        scenario_dir,
        allowed_symbols=universe_symbols,
    )
    universe_set = frozenset(universe)
    universe_bytes = _canonical_snapshot_bytes(universe)
    universe_sha256 = hashlib.sha256(universe_bytes).hexdigest()
    if (
        expected_universe_sha256 is not None
        and universe_sha256 != expected_universe_sha256.lower()
    ):
        raise ValueError(
            "scenario universe SHA256 mismatch: "
            f"expected {expected_universe_sha256.lower()}, got {universe_sha256}"
        )

    source = "alpaca"
    resolved_source_path: str | None = None
    if source_path is not None:
        loaded = load_fractionability_snapshot(source_path)
        if (
            expected_source_sha256 is not None
            and loaded.sha256 != expected_source_sha256.lower()
        ):
            raise ValueError(
                "fractionability source SHA256 mismatch: "
                f"expected {expected_source_sha256.lower()}, got {loaded.sha256}"
            )
        fractionable = loaded.symbols
        source = "file"
        resolved_source_path = str(loaded.path)
    else:
        if expected_source_sha256 is not None:
            raise ValueError("--expected-source-sha256 requires --source-file")
        resolver = resolve_fractionable or _resolve_broker_fractionable_symbols
        try:
            fractionable = frozenset(
                str(symbol).upper() for symbol in resolver(universe)
            )
        except Exception as exc:
            raise ValueError(
                f"could not resolve Alpaca fractionability for {len(universe)} symbols: {exc}"
            ) from exc

    unexpected = sorted(fractionable - universe_set)
    if unexpected:
        preview = ",".join(unexpected[:20])
        raise ValueError(
            "fractionability snapshot contains symbols outside the scenario universe: "
            f"{preview}"
        )

    canonical_bytes = _canonical_snapshot_bytes(sorted(fractionable))
    snapshot_sha256 = hashlib.sha256(canonical_bytes).hexdigest()
    non_fractionable = sorted(universe_set - fractionable)
    _write_bytes_atomic(output_path, canonical_bytes)
    if universe_output_path is not None:
        _write_bytes_atomic(universe_output_path, universe_bytes)

    metadata: dict[str, object] = {
        "schema_version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "source_path": resolved_source_path,
        "scenario_dir": str(scenario_dir.resolve()),
        "universe_symbol_count": len(universe),
        "universe_sha256": universe_sha256,
        "universe_symbols_file": (
            str(universe_output_path.resolve())
            if universe_output_path is not None
            else None
        ),
        "fractionable_symbol_count": len(fractionable),
        "non_fractionable_symbol_count": len(non_fractionable),
        "non_fractionable_symbols": non_fractionable,
        "snapshot_file": str(output_path.resolve()),
        "snapshot_sha256": snapshot_sha256,
    }
    _write_bytes_atomic(
        metadata_path,
        (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return metadata


def _resolve_broker_fractionable_symbols(symbols: Sequence[str]) -> frozenset[str]:
    settings = Settings.from_env()
    broker = AlpacaExecutionAdapter.from_settings(settings)
    return broker.get_fractionable_symbols(symbols, strict=True)


def resolve_active_watchlist_symbols() -> tuple[str, ...]:
    settings = Settings.from_env()
    try:
        connection = connect_postgres(settings.database_url)
        try:
            store = WatchlistStore(connection)
            enabled = store.list_enabled(settings.trading_mode.value)
            ignored = {
                str(symbol).upper()
                for symbol in store.list_ignored(settings.trading_mode.value)
            }
        finally:
            connection.close()
    except Exception as exc:
        raise ValueError(f"could not load active paper watchlist: {exc}") from exc
    symbols = tuple(
        sorted(
            {
                str(symbol).upper()
                for symbol in enabled
                if str(symbol).upper() not in ignored
            }
        )
    )
    if not symbols:
        raise ValueError("active paper watchlist is empty")
    return symbols


def _canonical_snapshot_bytes(symbols: Iterable[str]) -> bytes:
    normalized = sorted({str(symbol).upper() for symbol in symbols})
    return "".join(f"{symbol}\n" for symbol in normalized).encode("utf-8")


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        temporary_path.write_bytes(payload)
        temporary_path.replace(path)
    except OSError as exc:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise ValueError(f"could not write fractionability snapshot {path}: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="alpaca-bot-fractionability-snapshot")
    parser.add_argument("--scenario-dir", required=True, metavar="DIR")
    parser.add_argument("--output", required=True, metavar="FILE")
    parser.add_argument("--metadata-output", required=True, metavar="FILE")
    parser.add_argument("--universe-output", metavar="FILE")
    universe_group = parser.add_mutually_exclusive_group()
    universe_group.add_argument("--symbols-file", metavar="FILE")
    universe_group.add_argument("--active-watchlist", action="store_true")
    parser.add_argument("--source-file", metavar="FILE")
    parser.add_argument("--expected-source-sha256", metavar="SHA256")
    parser.add_argument("--expected-universe-sha256", metavar="SHA256")
    args = parser.parse_args(argv)

    try:
        universe_symbols = None
        if args.symbols_file:
            universe_symbols = load_fractionability_snapshot(
                Path(args.symbols_file)
            ).symbols
        elif args.active_watchlist:
            universe_symbols = resolve_active_watchlist_symbols()
        metadata = capture_fractionability_snapshot(
            scenario_dir=Path(args.scenario_dir),
            output_path=Path(args.output),
            metadata_path=Path(args.metadata_output),
            universe_output_path=(
                Path(args.universe_output) if args.universe_output else None
            ),
            universe_symbols=universe_symbols,
            source_path=Path(args.source_file) if args.source_file else None,
            expected_source_sha256=args.expected_source_sha256,
            expected_universe_sha256=args.expected_universe_sha256,
        )
    except ValueError as exc:
        print(f"fractionability snapshot failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(metadata, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
