from pathlib import Path


def test_cron_runs_session_guard_profit_probe_then_nightly() -> None:
    cron_text = Path("deploy/cron.d/alpaca-bot").read_text()

    readiness = "20 13 * * 1-5 root flock -n /var/lock/alpaca-bot-paper-readiness.lock"
    premarket = "30 13 * * 1-5 root flock -n /var/lock/alpaca-bot-nightly.lock"
    session_guard = "10 22 * * 1-5 root flock -n /var/lock/alpaca-bot-session-guard.lock"
    profit_probe = "20 22 * * 1-5 root flock -n /var/lock/alpaca-bot-profit-probe.lock"
    nightly = "30 22 * * 1-5 root flock -n /var/lock/alpaca-bot-nightly.lock"

    assert readiness in cron_text
    assert premarket in cron_text
    assert session_guard in cron_text
    assert profit_probe in cron_text
    assert nightly in cron_text
    assert cron_text.index(readiness) < cron_text.index(premarket)
    assert cron_text.index(session_guard) < cron_text.index(profit_probe)
    assert cron_text.index(profit_probe) < cron_text.index(nightly)
    assert "scripts/paper_readiness_check.sh" in cron_text
    assert "/var/log/alpaca-bot-paper-readiness.log" in cron_text
    assert "scripts/paper_profit_probe.sh" in cron_text
    assert "/var/log/alpaca-bot-profit-probe.log" in cron_text
