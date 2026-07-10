from __future__ import annotations

import itertools

import pytest

from alpaca_bot.nightly.setup_variants import VariantRow, stratified_variant_cap
from alpaca_bot.tuning.sweep import STRATEGY_GRIDS


def _rows(candidate: str, count: int) -> list[VariantRow]:
    return [
        (candidate, f"grid_{index:03d}", f"VALUE={index}")
        for index in range(count)
    ]


def test_stratified_variant_cap_preserves_uncapped_rows() -> None:
    rows = _rows("breakout", 2) + _rows("orb", 2)

    assert stratified_variant_cap(rows, 0) == rows
    assert stratified_variant_cap(rows, len(rows)) == rows


def test_stratified_variant_cap_balances_candidates_and_spreads_grids() -> None:
    rows = _rows("breakout", 6) + _rows("orb", 6) + _rows("bb_squeeze", 6)

    selected = stratified_variant_cap(rows, 6)

    assert [(candidate, label) for candidate, label, _overrides in selected] == [
        ("breakout", "grid_000"),
        ("orb", "grid_000"),
        ("bb_squeeze", "grid_000"),
        ("breakout", "grid_005"),
        ("orb", "grid_005"),
        ("bb_squeeze", "grid_005"),
    ]


def test_stratified_variant_cap_redistributes_slots_from_small_groups() -> None:
    rows = _rows("single", 1) + _rows("orb", 5) + _rows("bb_squeeze", 5)

    selected = stratified_variant_cap(rows, 7)

    assert [candidate for candidate, _label, _overrides in selected] == [
        "single",
        "orb",
        "bb_squeeze",
        "orb",
        "bb_squeeze",
        "orb",
        "bb_squeeze",
    ]
    assert [label for candidate, label, _overrides in selected if candidate == "orb"] == [
        "grid_000",
        "grid_002",
        "grid_004",
    ]


def test_stratified_variant_cap_uses_middle_when_only_one_slot_is_available() -> None:
    rows = _rows("breakout", 5) + _rows("orb", 5)

    assert stratified_variant_cap(rows, 1) == [rows[2]]


def test_stratified_variant_cap_rejects_negative_limit() -> None:
    with pytest.raises(ValueError, match="max_variants must be non-negative"):
        stratified_variant_cap(_rows("breakout", 2), -1)


def test_stratified_variant_cap_covers_real_second_strategy_grids() -> None:
    candidates = sorted(
        candidate
        for candidate in STRATEGY_GRIDS
        if candidate not in {"bull_flag", "vwap_cross"}
    )
    rows: list[VariantRow] = []
    last_label_by_candidate: dict[str, str] = {}
    for candidate in candidates:
        grid = STRATEGY_GRIDS[candidate]
        keys = list(grid)
        for index, combo in enumerate(
            itertools.product(*(grid[key] for key in keys)),
            start=1,
        ):
            label = f"grid_{index:03d}"
            overrides = ",".join(
                f"{key}={value}" for key, value in zip(keys, combo, strict=True)
            )
            rows.append((candidate, label, overrides))
            last_label_by_candidate[candidate] = label

    selected = stratified_variant_cap(rows, 48)
    selected_by_candidate = {
        candidate: [label for row_candidate, label, _overrides in selected if row_candidate == candidate]
        for candidate in candidates
    }

    assert len(selected) == 48
    assert set(selected_by_candidate) == set(candidates)
    assert max(map(len, selected_by_candidate.values())) - min(
        map(len, selected_by_candidate.values())
    ) <= 1
    assert all(labels[0] == "grid_001" for labels in selected_by_candidate.values())
    assert all(
        labels[-1] == last_label_by_candidate[candidate]
        for candidate, labels in selected_by_candidate.items()
    )
