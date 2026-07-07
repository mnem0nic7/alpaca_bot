from __future__ import annotations

import json
import logging
import os
import tempfile
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

        regime_symbol = self._settings.regime_symbol.upper()
        vix_symbol = self._settings.vix_proxy_symbol.upper()
        sector_symbols = [symbol.upper() for symbol in self._settings.sector_etf_symbols]
        daily_symbols = list(
            dict.fromkeys([*symbols, regime_symbol, vix_symbol, *sector_symbols])
        )
        daily_by_symbol = self._adapter.get_daily_bars(
            symbols=daily_symbols, start=start, end=end
        )
        intraday_by_symbol = self._adapter.get_stock_bars(
            symbols=list(symbols), start=start, end=end, timeframe_minutes=15
        )
        regime_daily = daily_by_symbol.get(regime_symbol, [])

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
                "regime_symbol": regime_symbol,
            }
            if regime_daily:
                payload["regime_daily_bars"] = [_bar_to_dict(b) for b in regime_daily]
            vix_daily = daily_by_symbol.get(vix_symbol, [])
            if vix_daily:
                payload["vix_proxy_symbol"] = vix_symbol
                payload["vix_daily_bars"] = [_bar_to_dict(b) for b in vix_daily]
            sector_daily_by_etf = {
                etf: daily_by_symbol.get(etf, [])
                for etf in sector_symbols
                if daily_by_symbol.get(etf)
            }
            if sector_daily_by_etf:
                payload["sector_daily_bars_by_etf"] = {
                    etf: [_bar_to_dict(b) for b in bars]
                    for etf, bars in sector_daily_by_etf.items()
                }
            path = output_dir / f"{symbol}_{days}d.json"
            _write_json_atomic(path, payload)
            results.append((path, len(intraday), len(daily)))

        return results

    def enrich_existing_scenarios_with_context(
        self,
        *,
        output_dir: Path,
        days: int,
        symbols: Sequence[str] | None = None,
    ) -> list[tuple[Path, int, int, int]]:
        """Add daily market-context bars to existing scenario files.

        Returns list of (path, n_regime, n_vix, n_sector_etfs) for each file
        updated. The scenario's own daily/intraday bars are left untouched.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        scenario_paths = _existing_scenario_paths(
            output_dir=output_dir,
            days=days,
            symbols=symbols,
        )
        if not scenario_paths:
            return []

        now = datetime.now(tz=timezone.utc)
        end = now
        calendar_days = int(days * 1.5) + 14
        start = now - timedelta(days=calendar_days)

        regime_symbol = self._settings.regime_symbol.upper()
        vix_symbol = self._settings.vix_proxy_symbol.upper()
        sector_symbols = [symbol.upper() for symbol in self._settings.sector_etf_symbols]
        daily_by_symbol = self._adapter.get_daily_bars(
            symbols=list(dict.fromkeys([regime_symbol, vix_symbol, *sector_symbols])),
            start=start,
            end=end,
        )
        regime_daily = daily_by_symbol.get(regime_symbol, [])
        vix_daily = daily_by_symbol.get(vix_symbol, [])
        sector_daily_by_etf = {
            etf: daily_by_symbol.get(etf, [])
            for etf in sector_symbols
            if daily_by_symbol.get(etf)
        }
        if not regime_daily and not vix_daily and not sector_daily_by_etf:
            return []

        results: list[tuple[Path, int, int, int]] = []
        for path in scenario_paths:
            try:
                payload = json.loads(path.read_text())
            except Exception as exc:
                logger.warning("Could not read scenario %s — skipping: %s", path, exc)
                continue

            if regime_daily:
                payload["regime_symbol"] = regime_symbol
                payload["regime_daily_bars"] = [_bar_to_dict(b) for b in regime_daily]
            if vix_daily:
                payload["vix_proxy_symbol"] = vix_symbol
                payload["vix_daily_bars"] = [_bar_to_dict(b) for b in vix_daily]
            if sector_daily_by_etf:
                payload["sector_daily_bars_by_etf"] = {
                    etf: [_bar_to_dict(b) for b in bars]
                    for etf, bars in sector_daily_by_etf.items()
                }
            _write_json_atomic(path, payload)
            results.append(
                (
                    path,
                    len(regime_daily),
                    len(vix_daily),
                    len(sector_daily_by_etf),
                )
            )

        return results


def _existing_scenario_paths(
    *,
    output_dir: Path,
    days: int,
    symbols: Sequence[str] | None,
) -> list[Path]:
    if symbols is not None:
        return [
            output_dir / f"{symbol.upper()}_{days}d.json"
            for symbol in symbols
            if (output_dir / f"{symbol.upper()}_{days}d.json").exists()
        ]
    return sorted(output_dir.glob(f"*_{days}d.json"))


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        tmp_path.chmod(mode)
        tmp_path.replace(path)
    finally:
        tmp_path.unlink(missing_ok=True)


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
