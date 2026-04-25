from __future__ import annotations

from alpaca_bot.web import cli


def test_web_cli_passes_host_and_port_to_uvicorn(monkeypatch) -> None:
    captured = {}

    def fake_run(app, *, factory, host, port) -> None:
        captured.update(
            {
                "app": app,
                "factory": factory,
                "host": host,
                "port": port,
            }
        )

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

    result = cli.main(["--host", "0.0.0.0", "--port", "9090"])

    assert result == 0
    assert captured == {
        "app": "alpaca_bot.web.app:create_app",
        "factory": True,
        "host": "0.0.0.0",
        "port": 9090,
    }
