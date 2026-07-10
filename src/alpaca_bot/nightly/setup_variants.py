from __future__ import annotations

from collections.abc import Sequence


VariantRow = tuple[str, str, str]


def _evenly_spaced(rows: Sequence[VariantRow], count: int) -> list[VariantRow]:
    if count <= 0:
        return []
    if count >= len(rows):
        return list(rows)
    if count == 1:
        return [rows[(len(rows) - 1) // 2]]

    last_index = len(rows) - 1
    return [rows[round(index * last_index / (count - 1))] for index in range(count)]


def stratified_variant_cap(
    rows: Sequence[VariantRow],
    max_variants: int,
) -> list[VariantRow]:
    """Cap a setup scan without allowing early candidates to consume every slot."""
    if max_variants < 0:
        raise ValueError("max_variants must be non-negative")
    if max_variants == 0 or len(rows) <= max_variants:
        return list(rows)

    grouped: dict[str, list[VariantRow]] = {}
    for row in rows:
        grouped.setdefault(row[0], []).append(row)

    candidate_order = list(grouped)
    quotas = dict.fromkeys(candidate_order, 0)
    remaining = max_variants
    while remaining:
        allocated = False
        for candidate in candidate_order:
            if quotas[candidate] >= len(grouped[candidate]):
                continue
            quotas[candidate] += 1
            remaining -= 1
            allocated = True
            if remaining == 0:
                break
        if not allocated:
            break

    selected = {
        candidate: _evenly_spaced(grouped[candidate], quotas[candidate])
        for candidate in candidate_order
    }
    capped: list[VariantRow] = []
    for index in range(max(quotas.values(), default=0)):
        for candidate in candidate_order:
            candidate_rows = selected[candidate]
            if index < len(candidate_rows):
                capped.append(candidate_rows[index])
    return capped
