from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar
from alpaca_bot.execution.alpaca import AlpacaMarketDataAdapter

logger = logging.getLogger(__name__)


class BackfillFetcher:
    def __init__(self, adapter: AlpacaMarketDataAdapter, settings: Settings) -> None:
        self._adapter = adapter
        self._settings = settings

    def fetch_and_save(
        self,
        *,
        symbols: Sequence[str],
        days: int,
        output_dir: Path,
        starting_equity: float = 100_000.0,
    ) -> list[tuple[Path, int, int]]:
        """Fetch bar data and write one scenario JSON per symbol.

        Returns list of (path, n_intraday, n_daily) for each file written.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(tz=timezone.utc)
        end = now
        # days is trading days; multiply by 1.5 to cover enough calendar days
        calendar_days = int(days * 1.5) + 14
        start = now - timedelta(days=calendar_days)

        daily_by_symbol = self._adapter.get_daily_bars(
            symbols=list(symbols), start=start, end=end
        )
        intraday_by_symbol = self._adapter.get_stock_bars(
            symbols=list(symbols), start=start, end=end, timeframe_minutes=15
        )

        results: list[tuple[Path, int, int]] = []
        for symbol in symbols:
            daily = daily_by_symbol.get(symbol, [])
            intraday = intraday_by_symbol.get(symbol, [])
            if not daily or not intraday:
                logger.warning("No bars returned for %s — skipping", symbol)
                continue

            payload = {
                "name": f"{symbol}_{days}d",
                "symbol": symbol,
                "starting_equity": starting_equity,
                "daily_bars": [_bar_to_dict(b) for b in daily],
                "intraday_bars": [_bar_to_dict(b) for b in intraday],
            }
            path = output_dir / f"{symbol}_{days}d.json"
            path.write_text(json.dumps(payload, indent=2))
            results.append((path, len(intraday), len(daily)))

        return results


def _bar_to_dict(bar: Bar) -> dict:
    return {
        "symbol": bar.symbol,
        "timestamp": bar.timestamp.isoformat(),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
    }
