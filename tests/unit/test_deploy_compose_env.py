from __future__ import annotations

import re
from pathlib import Path


def test_compose_passes_paper_edge_and_risk_env_vars() -> None:
    compose_text = Path("deploy/compose.yaml").read_text()
    passed_vars = set(re.findall(r"([A-Z][A-Z0-9_]*): \$\{", compose_text))

    expected = {
        "ENABLE_OPTIONS_TRADING",
        "ENABLE_SECTOR_FILTER",
        "ENABLE_VIX_FILTER",
        "ENABLE_VWAP_ENTRY_FILTER",
        "FLOOR_AUTO_RAISE_MAX_AGE_DAYS",
        "MAX_OPEN_POSITIONS",
        "ATR_STOP_MULTIPLIER",
        "TRAILING_STOP_ATR_MULTIPLIER",
        "OPTION_CHAIN_MIN_TOTAL_VOLUME",
        "OPTION_STRATEGY_MAX_ROLLING_LOSS_USD",
        "OPTION_STRATEGY_ROLLING_LOSS_DAYS",
        "PAPER_PROOF_FREEZE",
        "REPLAY_SLIPPAGE_BPS",
    }

    assert expected <= passed_vars
    assert "ATR_STOP_MULTIPLIER: ${ATR_STOP_MULTIPLIER:-1.0}" in compose_text
    assert (
        "TRAILING_STOP_ATR_MULTIPLIER: ${TRAILING_STOP_ATR_MULTIPLIER:-0.0}"
        in compose_text
    )


def test_deploy_ops_check_enforces_paper_readiness() -> None:
    deploy_text = Path("scripts/deploy.sh").read_text()

    assert 'REQUIRE_CRON_HEALTH="${REQUIRE_CRON_HEALTH:-true}"' in deploy_text
    assert "REQUIRE_CRON_HEALTH must be true or false" in deploy_text
    assert '"$ROOT_DIR/scripts/cron_health_check.sh"' in deploy_text
    assert "Cron health check skipped because REQUIRE_CRON_HEALTH=false" in deploy_text
    assert '--expect-trading-mode "${TRADING_MODE}"' in deploy_text
    assert '--expect-strategy-version "${STRATEGY_VERSION}"' in deploy_text
    assert "--expect-trading-status enabled" in deploy_text
    assert "--expect-kill-switch false" in deploy_text
    assert "--expect-only-enabled-strategy bull_flag" in deploy_text


def test_paper_env_example_matches_audited_bull_flag_posture() -> None:
    env_text = Path("deploy/paper.env.example").read_text()

    assert "MARKET_DATA_FEED=iex" in env_text
    assert "RELATIVE_VOLUME_THRESHOLD=2.0" in env_text
    assert "MAX_OPEN_POSITIONS=3" in env_text
    assert "REPLAY_SLIPPAGE_BPS=2.0" in env_text
    assert "PAPER_PROOF_FREEZE=true" in env_text
    assert "RISK_PER_TRADE_PCT=0.01" in env_text
    assert "MAX_POSITION_PCT=0.05" in env_text
    assert "MAX_PORTFOLIO_EXPOSURE_PCT=0.30" in env_text
    assert "INTRADAY_CONSECUTIVE_LOSS_GATE=0" in env_text
    assert "ATR_PERIOD=14" in env_text
    assert "ATR_STOP_MULTIPLIER=1.0" in env_text
    assert "TRAILING_STOP_ATR_MULTIPLIER=1.5" in env_text
    assert "TRAILING_STOP_PROFIT_TRIGGER_R=1.0" in env_text
    assert "ENABLE_VIX_FILTER=false" in env_text
    assert "ENABLE_SECTOR_FILTER=false" in env_text
    assert "ENABLE_VWAP_ENTRY_FILTER=true" in env_text
    assert "ENABLE_PROFIT_TRAIL=true" in env_text
    assert "PROFIT_TRAIL_PCT=0.95" in env_text
    assert "ENABLE_REGIME_FILTER=false" in env_text
    assert "ENABLE_NEWS_FILTER=false" in env_text
    assert "ENABLE_SPREAD_FILTER=false" in env_text
    assert "ENABLE_OPTIONS_TRADING=false" in env_text
    assert "OPTION_CHAIN_SYMBOLS=" in env_text


def test_init_server_generates_audited_paper_posture() -> None:
    script = Path("scripts/init_server.sh").read_text()

    assert "MARKET_DATA_FEED=iex" in script
    assert 'RISK_PER_TRADE_PCT="0.01"' in script
    assert 'MAX_OPEN_POSITIONS="3"' in script
    assert 'RELATIVE_VOLUME_THRESHOLD="2.0"' in script
    assert 'REPLAY_SLIPPAGE_BPS="2.0"' in script
    assert 'PAPER_PROOF_FREEZE="true"' in script
    assert "INTRADAY_CONSECUTIVE_LOSS_GATE=0" in script
    assert "ATR_PERIOD=14" in script
    assert "ATR_STOP_MULTIPLIER=1.0" in script
    assert "TRAILING_STOP_ATR_MULTIPLIER=1.5" in script
    assert "TRAILING_STOP_PROFIT_TRIGGER_R=1.0" in script
    assert 'ENABLE_VIX_FILTER="false"' in script
    assert 'ENABLE_SECTOR_FILTER="false"' in script
    assert 'ENABLE_VWAP_ENTRY_FILTER="true"' in script
    assert 'ENABLE_PROFIT_TRAIL="true"' in script
    assert "PROFIT_TRAIL_PCT=0.95" in script
    assert "ENABLE_NEWS_FILTER=false" in script
    assert "ENABLE_SPREAD_FILTER=false" in script
    assert "OPTION_CHAIN_SYMBOLS=" in script
