#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-/etc/alpaca_bot/alpaca-bot.env}"
MODE="${2:-paper}"

if [[ "$MODE" != "paper" && "$MODE" != "live" ]]; then
  echo "usage: $0 [env-file] [paper|live]" >&2
  exit 1
fi

mkdir -p "$(dirname "$ENV_FILE")"

if [[ -f "$ENV_FILE" ]]; then
  echo "env file already exists: $ENV_FILE" >&2
  exit 1
fi

POSTGRES_PASSWORD="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(24))
PY
)"

if [[ "$MODE" == "paper" ]]; then
  ENABLE_LIVE_TRADING="false"
else
  ENABLE_LIVE_TRADING="true"
fi

cat >"$ENV_FILE" <<EOF
TRADING_MODE=$MODE
ENABLE_LIVE_TRADING=$ENABLE_LIVE_TRADING
STRATEGY_VERSION=v1-breakout

POSTGRES_DB=alpaca_bot
POSTGRES_USER=alpaca_bot
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
DATABASE_URL=postgresql://alpaca_bot:$POSTGRES_PASSWORD@postgres:5432/alpaca_bot
MARKET_DATA_FEED=sip
SYMBOLS=AAPL,MSFT,AMZN,NVDA,META,SPY,QQQ,IWM

DAILY_SMA_PERIOD=20
BREAKOUT_LOOKBACK_BARS=20
RELATIVE_VOLUME_LOOKBACK_BARS=20
RELATIVE_VOLUME_THRESHOLD=1.5
ENTRY_TIMEFRAME_MINUTES=15

RISK_PER_TRADE_PCT=0.0025
MAX_POSITION_PCT=0.05
MAX_OPEN_POSITIONS=3
DAILY_LOSS_LIMIT_PCT=0.01
STOP_LIMIT_BUFFER_PCT=0.001
BREAKOUT_STOP_BUFFER_PCT=0.001
ENTRY_STOP_PRICE_BUFFER=0.01

ENTRY_WINDOW_START=10:00
ENTRY_WINDOW_END=15:30
FLATTEN_TIME=15:45

ALPACA_PAPER_API_KEY=replace_me
ALPACA_PAPER_SECRET_KEY=replace_me
ALPACA_LIVE_API_KEY=replace_me
ALPACA_LIVE_SECRET_KEY=replace_me
EOF

chmod 600 "$ENV_FILE"
echo "created $ENV_FILE"
echo "fill in the Alpaca keys before starting the supervisor"

