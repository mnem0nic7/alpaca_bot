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
        "OPTION_CHAIN_MIN_TOTAL_VOLUME",
        "OPTION_STRATEGY_MAX_ROLLING_LOSS_USD",
        "OPTION_STRATEGY_ROLLING_LOSS_DAYS",
        "REPLAY_SLIPPAGE_BPS",
    }

    assert expected <= passed_vars


def test_deploy_ops_check_enforces_paper_readiness() -> None:
    deploy_text = Path("scripts/deploy.sh").read_text()

    assert '--expect-trading-mode "${TRADING_MODE}"' in deploy_text
    assert '--expect-strategy-version "${STRATEGY_VERSION}"' in deploy_text
    assert "--expect-trading-status enabled" in deploy_text
    assert "--expect-kill-switch false" in deploy_text
    assert "--expect-only-enabled-strategy bull_flag" in deploy_text


def test_paper_env_example_matches_audited_bull_flag_posture() -> None:
    env_text = Path("deploy/paper.env.example").read_text()

    assert "MAX_OPEN_POSITIONS=2" in env_text
    assert "REPLAY_SLIPPAGE_BPS=2.0" in env_text
    assert "RISK_PER_TRADE_PCT=0.01" in env_text
    assert "MAX_POSITION_PCT=0.05" in env_text
    assert "MAX_PORTFOLIO_EXPOSURE_PCT=0.30" in env_text
    assert "ENABLE_VIX_FILTER=true" in env_text
    assert "ENABLE_SECTOR_FILTER=true" in env_text
    assert "ENABLE_VWAP_ENTRY_FILTER=true" in env_text
    assert "ENABLE_REGIME_FILTER=false" in env_text
    assert "ENABLE_OPTIONS_TRADING=false" in env_text


def test_init_server_generates_audited_paper_posture() -> None:
    script = Path("scripts/init_server.sh").read_text()

    assert 'RISK_PER_TRADE_PCT="0.01"' in script
    assert 'MAX_OPEN_POSITIONS="2"' in script
    assert 'REPLAY_SLIPPAGE_BPS="2.0"' in script
    assert 'ENABLE_VIX_FILTER="true"' in script
    assert 'ENABLE_SECTOR_FILTER="true"' in script
    assert 'ENABLE_VWAP_ENTRY_FILTER="true"' in script
