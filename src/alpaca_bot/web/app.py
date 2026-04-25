from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from alpaca_bot.config import Settings
from alpaca_bot.storage import (
    AuditEventStore,
    DailySessionStateStore,
    OrderStore,
    PositionStore,
    TradingStatusStore,
)
from alpaca_bot.storage.db import ConnectionProtocol, connect_postgres
from alpaca_bot.web.service import load_dashboard_snapshot, load_health_snapshot


def create_app(
    *,
    settings: Settings | None = None,
    connect: Callable[[str], ConnectionProtocol] | None = None,
    connection: ConnectionProtocol | None = None,
    db_connection: ConnectionProtocol | None = None,
    connect_postgres_fn: Callable[[str], ConnectionProtocol] | None = None,
    trading_status_store_factory: Callable[[ConnectionProtocol], object] | None = None,
    position_store_factory: Callable[[ConnectionProtocol], object] | None = None,
    order_store_factory: Callable[[ConnectionProtocol], object] | None = None,
    daily_session_state_store_factory: Callable[[ConnectionProtocol], object] | None = None,
    audit_event_store_factory: Callable[[ConnectionProtocol], object] | None = None,
) -> FastAPI:
    app_settings = settings or Settings.from_env()
    fixed_connection = connection or db_connection
    connector = (
        connect_postgres_fn
        or connect
        or (None if fixed_connection is None else (lambda _database_url: fixed_connection))
        or connect_postgres
    )
    templates = Jinja2Templates(directory=str(Path(__file__).with_name("templates")))
    templates.env.globals["format_timestamp"] = lambda value: _format_timestamp(
        value,
        settings=app_settings,
    )
    templates.env.globals["format_price"] = _format_price

    app = FastAPI(title="alpaca_bot dashboard")
    app.state.settings = app_settings
    app.state.connect_postgres = connector
    app.state.templates = templates
    app.state.trading_status_store_factory = (
        trading_status_store_factory or TradingStatusStore
    )
    app.state.position_store_factory = position_store_factory or PositionStore
    app.state.order_store_factory = order_store_factory or OrderStore
    app.state.daily_session_state_store_factory = (
        daily_session_state_store_factory or DailySessionStateStore
    )
    app.state.audit_event_store_factory = audit_event_store_factory or AuditEventStore

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request) -> HTMLResponse:
        try:
            snapshot = _load_snapshot(app)
        except Exception as exc:  # pragma: no cover - exercised via route test
            return HTMLResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content=(
                    "<html><body><h1>alpaca_bot dashboard unavailable</h1>"
                    f"<p>{exc}</p></body></html>"
                ),
            )
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "request": request,
                "settings": app_settings,
                "snapshot": snapshot,
            },
        )

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        try:
            trading_status = _load_health(app)
        except Exception as exc:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"status": "error", "reason": str(exc)},
            )
        return JSONResponse(
            {
                "status": "ok",
                "db": "ok",
                "database": "ok",
                "trading_mode": app_settings.trading_mode.value,
                "strategy_version": app_settings.strategy_version,
                "trading_status": None if trading_status is None else trading_status.status.value,
                "kill_switch_enabled": (
                    False if trading_status is None else trading_status.kill_switch_enabled
                ),
            }
        )

    return app


def _load_snapshot(app: FastAPI):
    connection = app.state.connect_postgres(app.state.settings.database_url)
    try:
        return load_dashboard_snapshot(
            settings=app.state.settings,
            connection=connection,
            trading_status_store=_build_store(
                app.state.trading_status_store_factory,
                connection,
            ),
            daily_session_state_store=_build_store(
                app.state.daily_session_state_store_factory,
                connection,
            ),
            position_store=_build_store(
                app.state.position_store_factory,
                connection,
            ),
            order_store=_build_store(
                app.state.order_store_factory,
                connection,
            ),
            audit_event_store=_build_store(
                app.state.audit_event_store_factory,
                connection,
            ),
        )
    finally:
        close = getattr(connection, "close", None)
        if callable(close):
            close()


def _load_health(app: FastAPI):
    connection = app.state.connect_postgres(app.state.settings.database_url)
    try:
        return load_health_snapshot(
            settings=app.state.settings,
            connection=connection,
            trading_status_store=_build_store(
                app.state.trading_status_store_factory,
                connection,
            ),
        )
    finally:
        close = getattr(connection, "close", None)
        if callable(close):
            close()


def _build_store(factory: object, connection: ConnectionProtocol) -> object:
    if callable(factory):
        try:
            return factory(connection)
        except TypeError:
            return factory()
    return factory


def _format_timestamp(value: datetime | None, *, settings: Settings) -> str:
    if value is None:
        return "n/a"
    return value.astimezone(settings.market_timezone).strftime("%Y-%m-%d %H:%M:%S %Z")


def _format_price(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${value:,.2f}"
