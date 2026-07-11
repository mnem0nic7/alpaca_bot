from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from alpaca_bot.replay.fractionability_snapshot import (
    capture_fractionability_snapshot,
    load_fractionability_snapshot,
)


def _write_scenarios(path: Path, *symbols: str) -> None:
    path.mkdir()
    for symbol in symbols:
        (path / f"{symbol}_252d.json").write_text("{}\n")


def test_capture_fractionability_snapshot_records_broker_lineage(tmp_path):
    scenario_dir = tmp_path / "scenarios"
    _write_scenarios(scenario_dir, "BBB", "AAA", "CCC")
    snapshot_path = tmp_path / "fractionable_symbols.txt"
    universe_path = tmp_path / "scenario_symbols.txt"
    metadata_path = tmp_path / "fractionability_snapshot.json"
    requested: list[tuple[str, ...]] = []

    def resolve(symbols):
        requested.append(tuple(symbols))
        return {"CCC", "AAA"}

    metadata = capture_fractionability_snapshot(
        scenario_dir=scenario_dir,
        output_path=snapshot_path,
        metadata_path=metadata_path,
        universe_output_path=universe_path,
        resolve_fractionable=resolve,
    )

    assert requested == [("AAA", "BBB", "CCC")]
    assert snapshot_path.read_text() == "AAA\nCCC\n"
    assert universe_path.read_text() == "AAA\nBBB\nCCC\n"
    expected_sha256 = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
    assert metadata == json.loads(metadata_path.read_text())
    assert metadata["source"] == "alpaca"
    assert metadata["universe_symbol_count"] == 3
    assert metadata["fractionable_symbol_count"] == 2
    assert metadata["non_fractionable_symbol_count"] == 1
    assert metadata["non_fractionable_symbols"] == ["BBB"]
    assert metadata["snapshot_sha256"] == expected_sha256
    assert metadata["universe_symbols_file"] == str(universe_path.resolve())


def test_capture_fractionability_snapshot_filters_to_frozen_universe(tmp_path):
    scenario_dir = tmp_path / "scenarios"
    _write_scenarios(scenario_dir, "AAA", "BBB", "STALE")
    requested: list[tuple[str, ...]] = []

    def resolve(symbols):
        requested.append(tuple(symbols))
        return {"AAA"}

    metadata = capture_fractionability_snapshot(
        scenario_dir=scenario_dir,
        output_path=tmp_path / "fractionable.txt",
        metadata_path=tmp_path / "metadata.json",
        universe_output_path=tmp_path / "universe.txt",
        universe_symbols={"AAA", "BBB"},
        resolve_fractionable=resolve,
    )

    assert requested == [("AAA", "BBB")]
    assert (tmp_path / "universe.txt").read_text() == "AAA\nBBB\n"
    assert metadata["universe_symbol_count"] == 2
    assert metadata["non_fractionable_symbols"] == ["BBB"]


def test_capture_fractionability_snapshot_freezes_verified_source(tmp_path):
    scenario_dir = tmp_path / "scenarios"
    _write_scenarios(scenario_dir, "AAA", "BBB")
    source_path = tmp_path / "source.txt"
    source_path.write_text("bbb, AAA # broker export\n")
    source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
    snapshot_path = tmp_path / "frozen.txt"
    metadata_path = tmp_path / "metadata.json"

    metadata = capture_fractionability_snapshot(
        scenario_dir=scenario_dir,
        output_path=snapshot_path,
        metadata_path=metadata_path,
        source_path=source_path,
        expected_source_sha256=source_sha256,
    )

    assert snapshot_path.read_text() == "AAA\nBBB\n"
    assert metadata["source"] == "file"
    assert metadata["source_path"] == str(source_path.resolve())


def test_capture_fractionability_snapshot_rejects_source_sha_mismatch(tmp_path):
    scenario_dir = tmp_path / "scenarios"
    _write_scenarios(scenario_dir, "AAA")
    source_path = tmp_path / "source.txt"
    source_path.write_text("AAA\n")

    with pytest.raises(ValueError, match="source SHA256 mismatch"):
        capture_fractionability_snapshot(
            scenario_dir=scenario_dir,
            output_path=tmp_path / "snapshot.txt",
            metadata_path=tmp_path / "metadata.json",
            source_path=source_path,
            expected_source_sha256="0" * 64,
        )


def test_capture_fractionability_snapshot_rejects_universe_sha_mismatch(tmp_path):
    scenario_dir = tmp_path / "scenarios"
    _write_scenarios(scenario_dir, "AAA")

    with pytest.raises(ValueError, match="scenario universe SHA256 mismatch"):
        capture_fractionability_snapshot(
            scenario_dir=scenario_dir,
            output_path=tmp_path / "snapshot.txt",
            metadata_path=tmp_path / "metadata.json",
            expected_universe_sha256="0" * 64,
            resolve_fractionable=lambda _symbols: {"AAA"},
        )


def test_capture_fractionability_snapshot_rejects_symbols_outside_universe(tmp_path):
    scenario_dir = tmp_path / "scenarios"
    _write_scenarios(scenario_dir, "AAA")
    source_path = tmp_path / "source.txt"
    source_path.write_text("AAA\nZZZ\n")

    with pytest.raises(ValueError, match="outside the scenario universe: ZZZ"):
        capture_fractionability_snapshot(
            scenario_dir=scenario_dir,
            output_path=tmp_path / "snapshot.txt",
            metadata_path=tmp_path / "metadata.json",
            source_path=source_path,
        )


def test_capture_fractionability_snapshot_rejects_duplicate_scenario_symbol(tmp_path):
    scenario_dir = tmp_path / "scenarios"
    _write_scenarios(scenario_dir, "AAA")
    (scenario_dir / "AAA.json").write_text("{}\n")

    with pytest.raises(ValueError, match="duplicate scenario symbol AAA"):
        capture_fractionability_snapshot(
            scenario_dir=scenario_dir,
            output_path=tmp_path / "snapshot.txt",
            metadata_path=tmp_path / "metadata.json",
            resolve_fractionable=lambda _symbols: {"AAA"},
        )


def test_load_fractionability_snapshot_accepts_empty_whole_share_snapshot(tmp_path):
    snapshot_path = tmp_path / "empty.txt"
    snapshot_path.write_text("")

    snapshot = load_fractionability_snapshot(snapshot_path)

    assert snapshot.symbols == frozenset()
    assert snapshot.sha256 == hashlib.sha256(b"").hexdigest()
