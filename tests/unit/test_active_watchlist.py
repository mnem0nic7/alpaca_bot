from __future__ import annotations

from alpaca_bot.replay import active_watchlist


def test_active_watchlist_cli_writes_canonical_symbols(monkeypatch, capsys):
    monkeypatch.setattr(
        active_watchlist,
        "resolve_active_watchlist_symbols",
        lambda: ("AAA", "BBB"),
    )

    assert active_watchlist.main() == 0
    assert capsys.readouterr().out == "AAA\nBBB\n"


def test_active_watchlist_cli_fails_closed(monkeypatch, capsys):
    def fail():
        raise ValueError("database unavailable")

    monkeypatch.setattr(active_watchlist, "resolve_active_watchlist_symbols", fail)

    assert active_watchlist.main() == 1
    assert "active watchlist snapshot failed: database unavailable" in capsys.readouterr().err
