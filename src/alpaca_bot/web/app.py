from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl

from fastapi import FastAPI, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from alpaca_bot.config import Settings
from alpaca_bot.storage import (
    AuditEvent,
    AuditEventStore,
    DailySessionStateStore,
    OrderStore,
    PositionStore,
    StrategyFlag,
    StrategyFlagStore,
    TradingStatusStore,
)
from alpaca_bot.storage.db import ConnectionProtocol, connect_postgres, execute
from alpaca_bot.strategy import STRATEGY_REGISTRY
from alpaca_bot.web.auth import (
    authenticate_operator,
    auth_enabled,
    clear_operator_session,
    csrf_token_for_session,
    current_operator,
    set_operator_session,
    validate_csrf_token,
)
from alpaca_bot.web.service import (
    load_dashboard_snapshot,
    load_health_snapshot,
    load_metrics_snapshot,
)


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
    strategy_flag_store_factory: Callable[[ConnectionProtocol], object] | None = None,
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
    templates.env.globals["csrf_token_for"] = lambda request, action: csrf_token_for_session(
        request,
        settings=app_settings,
        action=action,
    )

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
    app.state.strategy_flag_store_factory = strategy_flag_store_factory or StrategyFlagStore

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request) -> HTMLResponse:
        operator = current_operator(request, settings=app_settings)
        if auth_enabled(app_settings) and operator is None:
            return _render_login_page(
                app,
                request,
                next_path=request.url.path,
            )
        try:
            snapshot, metrics = _load_dashboard_data(app)
        except Exception:
            return HTMLResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content=(
                    "<html><body><h1>alpaca_bot dashboard unavailable</h1>"
                    "<p>Service temporarily unavailable.</p></body></html>"
                ),
            )
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "request": request,
                "settings": app_settings,
                "snapshot": snapshot,
                "metrics": metrics,
                "operator_email": operator,
            },
        )

    @app.get("/metrics", response_class=HTMLResponse)
    def metrics_page(request: Request) -> HTMLResponse:
        operator = current_operator(request, settings=app_settings)
        if auth_enabled(app_settings) and operator is None:
            return _render_login_page(
                app,
                request,
                next_path=request.url.path,
            )
        try:
            _, metrics = _load_dashboard_data(app)
        except Exception:
            return HTMLResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content=(
                    "<html><body><h1>alpaca_bot metrics unavailable</h1>"
                    "<p>Service temporarily unavailable.</p></body></html>"
                ),
            )
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "request": request,
                "settings": app_settings,
                "snapshot": None,
                "metrics": metrics,
                "operator_email": operator,
            },
        )

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, next: str = "/") -> Response:
        if not auth_enabled(app_settings):
            return RedirectResponse(url=_local_path(next), status_code=status.HTTP_303_SEE_OTHER)
        operator = current_operator(request, settings=app_settings)
        if operator is not None:
            return RedirectResponse(url=_local_path(next), status_code=status.HTTP_303_SEE_OTHER)
        return _render_login_page(app, request, next_path=next)

    @app.post("/login")
    async def login(request: Request) -> Response:
        fields = await _read_form_fields(request)
        username = fields.get("username", "")
        password = fields.get("password", "")
        next_path = _local_path(fields.get("next"))

        if not auth_enabled(app_settings):
            return RedirectResponse(url=next_path, status_code=status.HTTP_303_SEE_OTHER)
        if not authenticate_operator(
            settings=app_settings,
            username=username,
            password=password,
        ):
            return _render_login_page(
                app,
                request,
                next_path=next_path,
                username=username,
                error_message="Invalid username or password.",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        response = RedirectResponse(url=next_path, status_code=status.HTTP_303_SEE_OTHER)
        set_operator_session(response, settings=app_settings, username=username)
        return response

    @app.post("/logout")
    async def logout(request: Request) -> Response:
        fields = await _read_form_fields(request)
        token = fields.get("_csrf_token", "")
        if auth_enabled(app_settings) and not validate_csrf_token(
            request, token, settings=app_settings, action="logout"
        ):
            return HTMLResponse(status_code=status.HTTP_403_FORBIDDEN, content="Forbidden")
        response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
        clear_operator_session(response)
        return response

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        try:
            health_snapshot = _load_health(app)
        except Exception as exc:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"status": "error", "reason": str(exc)},
            )
        worker_stale = health_snapshot.worker_health.status == "stale"
        http_status = (
            status.HTTP_503_SERVICE_UNAVAILABLE if worker_stale else status.HTTP_200_OK
        )
        return JSONResponse(
            {
                "status": "stale" if worker_stale else "ok",
                "db": "ok",
                "database": "ok",
                "trading_mode": app_settings.trading_mode.value,
                "strategy_version": app_settings.strategy_version,
                "trading_status": (
                    None
                    if health_snapshot.trading_status is None
                    else health_snapshot.trading_status.status.value
                ),
                "kill_switch_enabled": (
                    False
                    if health_snapshot.trading_status is None
                    else health_snapshot.trading_status.kill_switch_enabled
                ),
                "worker_status": health_snapshot.worker_health.status,
                "worker_last_event_type": health_snapshot.worker_health.last_event_type,
                "worker_last_event_at": (
                    None
                    if health_snapshot.worker_health.last_event_at is None
                    else health_snapshot.worker_health.last_event_at.isoformat()
                ),
                "worker_age_seconds": health_snapshot.worker_health.age_seconds,
            },
            status_code=http_status,
        )

    @app.post("/strategies/{strategy_name}/toggle")
    async def toggle_strategy(strategy_name: str, request: Request) -> Response:
        operator = current_operator(request, settings=app_settings)
        if auth_enabled(app_settings) and operator is None:
            return RedirectResponse(url="/login?next=/", status_code=status.HTTP_303_SEE_OTHER)
        fields = await _read_form_fields(request)
        token = fields.get("_csrf_token", "")
        if auth_enabled(app_settings) and not validate_csrf_token(
            request, token, settings=app_settings, action="toggle"
        ):
            return HTMLResponse(status_code=status.HTTP_403_FORBIDDEN, content="Forbidden")
        if strategy_name not in STRATEGY_REGISTRY:
            return HTMLResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content="Unknown strategy",
            )
        now = datetime.now(timezone.utc)
        connection = app.state.connect_postgres(app_settings.database_url)
        try:
            flag_store = _build_store(app.state.strategy_flag_store_factory, connection)
            audit_store = _build_store(app.state.audit_event_store_factory, connection)
            current_flag = flag_store.load(
                strategy_name=strategy_name,
                trading_mode=app_settings.trading_mode,
                strategy_version=app_settings.strategy_version,
            )
            new_enabled = not (current_flag.enabled if current_flag is not None else True)
            execute(connection, "BEGIN")
            try:
                flag_store.save(
                    StrategyFlag(
                        strategy_name=strategy_name,
                        trading_mode=app_settings.trading_mode,
                        strategy_version=app_settings.strategy_version,
                        enabled=new_enabled,
                        updated_at=now,
                    )
                )
                audit_store.append(
                    AuditEvent(
                        event_type="strategy_flag_changed",
                        payload={
                            "strategy_name": strategy_name,
                            "enabled": new_enabled,
                            "operator": operator or "web",
                        },
                        created_at=now,
                    )
                )
                execute(connection, "COMMIT")
            except Exception:
                execute(connection, "ROLLBACK")
                raise
        finally:
            close = getattr(connection, "close", None)
            if callable(close):
                close()
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    return app


def _load_dashboard_data(app: FastAPI) -> tuple:
    connection = app.state.connect_postgres(app.state.settings.database_url)
    try:
        order_store = _build_store(app.state.order_store_factory, connection)
        audit_event_store = _build_store(app.state.audit_event_store_factory, connection)
        snapshot = load_dashboard_snapshot(
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
            order_store=order_store,
            audit_event_store=audit_event_store,
            strategy_flag_store=_build_store(
                app.state.strategy_flag_store_factory,
                connection,
            ),
        )
        metrics = load_metrics_snapshot(
            settings=app.state.settings,
            connection=connection,
            order_store=order_store,
            audit_event_store=audit_event_store,
        )
        return snapshot, metrics
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
            audit_event_store=_build_store(
                app.state.audit_event_store_factory,
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


def _render_login_page(
    app: FastAPI,
    request: Request,
    *,
    next_path: str,
    username: str = "",
    error_message: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    return app.state.templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "request": request,
            "settings": app.state.settings,
            "next_path": _local_path(next_path),
            "username": username,
            "error_message": error_message,
        },
        status_code=status_code,
    )


async def _read_form_fields(request: Request) -> dict[str, str]:
    body = await request.body()
    return {
        key: value
        for key, value in parse_qsl(
            body.decode("utf-8"),
            keep_blank_values=True,
        )
    }


def _local_path(next_path: str | None) -> str:
    if not next_path or not next_path.startswith("/") or next_path.startswith("//"):
        return "/"
    return next_path
