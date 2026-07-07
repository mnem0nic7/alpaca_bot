from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import json
from pathlib import Path

from alpaca_bot.domain.models import OptionContract


def append_option_chain_snapshot(
    *,
    snapshot_dir: str | Path,
    cycle_at: datetime,
    chains_by_symbol: Mapping[str, Sequence[OptionContract]],
) -> Path:
    """Append one replay-grade option-chain snapshot to a UTC-dated JSONL file."""
    root = Path(snapshot_dir)
    root.mkdir(parents=True, exist_ok=True)
    cycle_utc = (
        cycle_at.replace(tzinfo=timezone.utc)
        if cycle_at.tzinfo is None
        else cycle_at.astimezone(timezone.utc)
    )
    path = root / f"option-chain-snapshots-{cycle_utc.date().isoformat()}.jsonl"
    payload = {
        "cycle_at": cycle_utc.isoformat(),
        "chains_by_symbol": {
            symbol: [_contract_payload(contract) for contract in contracts]
            for symbol, contracts in sorted(chains_by_symbol.items())
        },
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        handle.write("\n")
    return path


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
