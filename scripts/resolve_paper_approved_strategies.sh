#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${1:-/etc/alpaca_bot/alpaca-bot.env}"
FALLBACK_STRATEGIES="${2:-${PAPER_APPROVED_STRATEGIES:-bull_flag}}"

if [[ -f "$ENV_FILE" && ( -z "${TRADING_MODE+x}" || -z "${STRATEGY_VERSION+x}" || -z "${DATABASE_URL+x}" || -z "${SYMBOLS+x}" ) ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

default_marker="${SECOND_STRATEGY_OUTPUT_ROOT:-/var/lib/alpaca-bot/nightly/second_strategy}/promotion_approval.json"
if [[ -z "${PAPER_APPROVED_STRATEGIES+x}" \
  && -z "${PAPER_APPROVED_STRATEGIES_APPROVAL_MARKER+x}" \
  && ! -f "$default_marker" ]]; then
  printf '%s\n' "$FALLBACK_STRATEGIES"
  exit 0
fi
export PAPER_APPROVED_STRATEGIES="${PAPER_APPROVED_STRATEGIES:-$FALLBACK_STRATEGIES}"
export PAPER_APPROVED_STRATEGIES_APPROVAL_MARKER="${PAPER_APPROVED_STRATEGIES_APPROVAL_MARKER:-$default_marker}"

PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}" python3 - <<'PY'
from alpaca_bot.config import Settings

settings = Settings.from_env()
print(",".join(settings.paper_approved_strategies))
PY
