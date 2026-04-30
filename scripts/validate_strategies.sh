#!/usr/bin/env bash
# Runs multi-symbol strategy validation and writes a markdown report.
# Usage: ./scripts/validate_strategies.sh
set -euo pipefail

REPORT="docs/validation-report-$(date +%Y-%m-%d).md"
SCENARIO_DIR="data/backfill"

# Minimal env for replay (no DB connection made, no live trading possible)
# ALPACA_PAPER_API_KEY is optional (str | None) - not needed for replay
# DATABASE_URL is required by Settings but never used during replay
export TRADING_MODE=paper
export ENABLE_LIVE_TRADING=false
export STRATEGY_VERSION=v1-validate
export DATABASE_URL="postgresql://dummy:dummy@localhost/dummy"
export SYMBOLS=AAPL
export MARKET_DATA_FEED=sip

echo "# Strategy Validation Report" > "$REPORT"
echo "" >> "$REPORT"
echo "Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$REPORT"
echo "" >> "$REPORT"

echo "## Strategy Comparison (all 252d scenarios)" >> "$REPORT"
echo "" >> "$REPORT"

for f in "$SCENARIO_DIR"/*_252d.json; do
    symbol=$(basename "$f" _252d.json)
    echo "### $symbol" >> "$REPORT"
    echo '```' >> "$REPORT"
    alpaca-bot-backtest compare --scenario "$f" --format csv 2>/dev/null >> "$REPORT" || \
        echo "ERROR: compare failed for $f" >> "$REPORT"
    echo '```' >> "$REPORT"
    echo "" >> "$REPORT"
done

echo "## Parameter Sweep — Breakout (all 252d scenarios)" >> "$REPORT"
echo "" >> "$REPORT"
echo '```' >> "$REPORT"
alpaca-bot-sweep --scenario-dir "$SCENARIO_DIR" --strategy breakout 2>/dev/null >> "$REPORT" || \
    echo "ERROR: sweep failed" >> "$REPORT"
echo '```' >> "$REPORT"
echo "" >> "$REPORT"

echo "## Parameter Sweep — Momentum (all 252d scenarios)" >> "$REPORT"
echo "" >> "$REPORT"
echo '```' >> "$REPORT"
alpaca-bot-sweep --scenario-dir "$SCENARIO_DIR" --strategy momentum 2>/dev/null >> "$REPORT" || \
    echo "ERROR: sweep failed" >> "$REPORT"
echo '```' >> "$REPORT"
echo "" >> "$REPORT"

echo "## Parameter Sweep — ORB (all 252d scenarios)" >> "$REPORT"
echo "" >> "$REPORT"
echo '```' >> "$REPORT"
alpaca-bot-sweep --scenario-dir "$SCENARIO_DIR" --strategy orb 2>/dev/null >> "$REPORT" || \
    echo "ERROR: sweep failed" >> "$REPORT"
echo '```' >> "$REPORT"

echo "" >> "$REPORT"
echo "Report written to: $REPORT"
