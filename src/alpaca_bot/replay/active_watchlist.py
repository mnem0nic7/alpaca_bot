from __future__ import annotations

import sys

from alpaca_bot.replay.fractionability_snapshot import (
    resolve_active_watchlist_symbols,
)


def main() -> int:
    try:
        symbols = resolve_active_watchlist_symbols()
    except ValueError as exc:
        print(f"active watchlist snapshot failed: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write("".join(f"{symbol}\n" for symbol in symbols))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
