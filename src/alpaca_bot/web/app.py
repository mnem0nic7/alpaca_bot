from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import secrets
from urllib.parse import parse_qsl

from fastapi import FastAPI, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from alpaca_bot.config import Settings
from alpaca_bot.notifications import Notifier
from alpaca_bot.notifications.factory import build_notifier
import re

from alpaca_bot.storage import (
    AuditEvent,
    AuditEventStore,
    DailySessionState,
    DailySessionStateStore,
    OrderStore,
    PositionStore,
    StrategyFlag,
    StrategyFlagStore,
    TradingStatus,
    TradingStatusStore,
    TradingStatusValue,
    WatchlistStore,
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
    ALL_AUDIT_EVENT_TYPES,
    load_audit_page,
    load_dashboard_snapshot,
    load_health_snapshot,
    load_metrics_snapshot,
)


_SYMBOL_RE = re.compile(r"^[A-Z]{1,5}$")


def _validate_symbol(raw: str) -> str | None:
    cleaned = raw.strip().upper()
    return cleaned if _SYMBOL_RE.match(cleaned) else None


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
    watchlist_store_factory: Callable[[ConnectionProtocol], object] | None = None,
    notifier: Notifier | None = None,
    market_data_adapter: object | None = None,
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
    csrf_secret = secrets.token_bytes(32)
    templates.env.globals["csrf_token_for"] = lambda request, action: csrf_token_for_session(
        request,
        settings=app_settings,
        action=action,
        csrf_secret=csrf_secret,
    )

    app = FastAPI(title="alpaca_bot dashboard")
    app.state.settings = app_settings
    app.state.connect_postgres = connector
    app.state.templates = templates
    app.state.csrf_secret = csrf_secret
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
    app.state.watchlist_store_factory = watchlist_store_factory or WatchlistStore
    app.state.notifier = notifier or build_notifier(app_settings)
    if market_data_adapter is None:
        try:
            from alpaca_bot.execution.alpaca import AlpacaMarketDataAdapter
            market_data_adapter = AlpacaMarketDataAdapter.from_settings(app_settings)
        except Exception:
            market_data_adapter = None
    app.state.market_data_adapter = market_data_adapter

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, no_refresh: str = "") -> HTMLResponse:
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
                "trading_mode": app_settings.trading_mode.value,
                "strategy_version": app_settings.strategy_version,
                "snapshot": snapshot,
                "metrics": metrics,
                "operator_email": operator,
                "auto_refresh": not bool(no_refresh),
            },
        )

    @app.get("/metrics", response_class=HTMLResponse)
    def metrics_page(request: Request, date_param: str = "") -> HTMLResponse:
        operator = current_operator(request, settings=app_settings)
        if auth_enabled(app_settings) and operator is None:
            return _render_login_page(
                app,
                request,
                next_path=request.url.path,
            )
        now = datetime.now(timezone.utc)
        today = now.astimezone(app_settings.market_timezone).date()
        session_date, date_warning = _parse_date_param(date_param, today=today)
        try:
            connection = app.state.connect_postgres(app_settings.database_url)
            try:
                metrics = load_metrics_snapshot(
                    settings=app_settings,
                    connection=connection,
                    audit_event_store=_build_store(app.state.audit_event_store_factory, connection),
                    order_store=_build_store(app.state.order_store_factory, connection),
                    session_date=session_date,
                )
            finally:
                close = getattr(connection, "close", None)
                if callable(close):
                    close()
        except Exception:
            return HTMLResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content=(
                    "<html><body><h1>alpaca_bot metrics unavailable</h1>"
                    "<p>Service temporarily unavailable.</p></body></html>"
                ),
            )
        prev_date = _isoformat(session_date - timedelta(days=1))
        next_date_obj = session_date + timedelta(days=1)
        next_date = _isoformat(next_date_obj) if next_date_obj <= today else None
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "request": request,
                "settings": app_settings,
                "snapshot": None,
                "metrics": metrics,
                "operator_email": operator,
                "session_date": session_date.isoformat(),
                "today": today.isoformat(),
                "prev_date": prev_date,
                "next_date": next_date,
                "date_warning": date_warning,
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
        if not validate_csrf_token(
            request, token, settings=app_settings, action="logout", csrf_secret=csrf_secret
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
                "strategy_flags": [
                    {"name": name, "enabled": enabled}
                    for name, enabled in health_snapshot.strategy_flags
                ],
                "stream_stale": health_snapshot.stream_stale,
                "stream_last_stale_at": (
                    None
                    if health_snapshot.stream_last_stale_at is None
                    else health_snapshot.stream_last_stale_at.isoformat()
                ),
            },
            status_code=http_status,
        )

    @app.get("/audit", response_class=HTMLResponse)
    def audit_log(
        request: Request,
        limit: int = 50,
        offset: int = 0,
        event_type: str = "",
    ) -> HTMLResponse:
        operator = current_operator(request, settings=app_settings)
        if auth_enabled(app_settings) and operator is None:
            return _render_login_page(app, request, next_path=request.url.path)
        try:
            connection = app.state.connect_postgres(app_settings.database_url)
            try:
                page = load_audit_page(
                    connection=connection,
                    audit_event_store=_build_store(app.state.audit_event_store_factory, connection),
                    limit=max(1, min(limit, 200)),
                    offset=max(0, offset),
                    event_type_filter=event_type or None,
                )
            finally:
                close = getattr(connection, "close", None)
                if callable(close):
                    close()
        except Exception:
            return HTMLResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content="<html><body><h1>Audit log unavailable</h1></body></html>",
            )
        return templates.TemplateResponse(
            request=request,
            name="audit.html",
            context={
                "request": request,
                "trading_mode": app_settings.trading_mode.value,
                "strategy_version": app_settings.strategy_version,
                "operator_email": operator,
                "page": page,
                "all_event_types": ALL_AUDIT_EVENT_TYPES,
            },
        )

    @app.post("/admin/halt")
    async def admin_halt(request: Request) -> Response:
        operator = current_operator(request, settings=app_settings)
        if auth_enabled(app_settings) and operator is None:
            return RedirectResponse(url="/login?next=/", status_code=status.HTTP_303_SEE_OTHER)
        fields = await _read_form_fields(request)
        token = fields.get("_csrf_token", "")
        if not validate_csrf_token(
            request, token, settings=app_settings, action="admin", csrf_secret=csrf_secret
        ):
            return HTMLResponse(status_code=status.HTTP_403_FORBIDDEN, content="Forbidden")
        reason = fields.get("reason", "").strip()
        if not reason:
            return HTMLResponse(status_code=status.HTTP_400_BAD_REQUEST, content="reason is required")
        return _execute_admin_status_change(
            app,
            new_status=TradingStatusValue.HALTED,
            command_name="halt",
            kill_switch_enabled=True,
            reason=reason,
            operator=operator,
            notifier=app.state.notifier,
        )

    @app.post("/admin/resume")
    async def admin_resume(request: Request) -> Response:
        operator = current_operator(request, settings=app_settings)
        if auth_enabled(app_settings) and operator is None:
            return RedirectResponse(url="/login?next=/", status_code=status.HTTP_303_SEE_OTHER)
        fields = await _read_form_fields(request)
        token = fields.get("_csrf_token", "")
        if not validate_csrf_token(
            request, token, settings=app_settings, action="admin", csrf_secret=csrf_secret
        ):
            return HTMLResponse(status_code=status.HTTP_403_FORBIDDEN, content="Forbidden")
        reason = fields.get("reason", "").strip()
        return _execute_admin_status_change(
            app,
            new_status=TradingStatusValue.ENABLED,
            command_name="resume",
            kill_switch_enabled=False,
            reason=reason or None,
            operator=operator,
            notifier=app.state.notifier,
        )

    @app.post("/admin/close-only")
    async def admin_close_only(request: Request) -> Response:
        operator = current_operator(request, settings=app_settings)
        if auth_enabled(app_settings) and operator is None:
            return RedirectResponse(url="/login?next=/", status_code=status.HTTP_303_SEE_OTHER)
        fields = await _read_form_fields(request)
        token = fields.get("_csrf_token", "")
        if not validate_csrf_token(
            request, token, settings=app_settings, action="admin", csrf_secret=csrf_secret
        ):
            return HTMLResponse(status_code=status.HTTP_403_FORBIDDEN, content="Forbidden")
        reason = fields.get("reason", "").strip()
        return _execute_admin_status_change(
            app,
            new_status=TradingStatusValue.CLOSE_ONLY,
            command_name="close-only",
            kill_switch_enabled=False,
            reason=reason or None,
            operator=operator,
            notifier=app.state.notifier,
        )

    @app.post("/strategies/{strategy_name}/toggle-entries")
    async def toggle_strategy_entries(strategy_name: str, request: Request) -> Response:
        operator = current_operator(request, settings=app_settings)
        if auth_enabled(app_settings) and operator is None:
            return RedirectResponse(url="/login?next=/", status_code=status.HTTP_303_SEE_OTHER)
        fields = await _read_form_fields(request)
        token = fields.get("_csrf_token", "")
        if not validate_csrf_token(
            request, token, settings=app_settings, action="toggle", csrf_secret=csrf_secret
        ):
            return HTMLResponse(status_code=status.HTTP_403_FORBIDDEN, content="Forbidden")
        if strategy_name not in STRATEGY_REGISTRY:
            return HTMLResponse(status_code=status.HTTP_404_NOT_FOUND, content="Unknown strategy")
        now = datetime.now(timezone.utc)
        session_date = now.astimezone(app_settings.market_timezone).date()
        connection = None
        try:
            connection = app.state.connect_postgres(app_settings.database_url)
            state_store = _build_store(app.state.daily_session_state_store_factory, connection)
            audit_store = _build_store(app.state.audit_event_store_factory, connection)
            current_state = state_store.load(
                session_date=session_date,
                trading_mode=app_settings.trading_mode,
                strategy_version=app_settings.strategy_version,
                strategy_name=strategy_name,
            )
            new_entries_disabled = not (
                current_state.entries_disabled if current_state is not None else False
            )
            new_state = DailySessionState(
                session_date=session_date,
                trading_mode=app_settings.trading_mode,
                strategy_version=app_settings.strategy_version,
                strategy_name=strategy_name,
                entries_disabled=new_entries_disabled,
                flatten_complete=current_state.flatten_complete if current_state else False,
                last_reconciled_at=current_state.last_reconciled_at if current_state else None,
                notes=current_state.notes if current_state else None,
                equity_baseline=current_state.equity_baseline if current_state else None,
                updated_at=now,
            )
            state_store.save(new_state, commit=False)
            audit_store.append(
                AuditEvent(
                    event_type="strategy_entries_changed",
                    payload={
                        "strategy_name": strategy_name,
                        "entries_disabled": new_entries_disabled,
                        "operator": operator or "web",
                    },
                    created_at=now,
                ),
                commit=False,
            )
            connection.commit()
        finally:
            close = getattr(connection, "close", None)
            if callable(close):
                close()
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/strategies/{strategy_name}/toggle")
    async def toggle_strategy(strategy_name: str, request: Request) -> Response:
        operator = current_operator(request, settings=app_settings)
        if auth_enabled(app_settings) and operator is None:
            return RedirectResponse(url="/login?next=/", status_code=status.HTTP_303_SEE_OTHER)
        fields = await _read_form_fields(request)
        token = fields.get("_csrf_token", "")
        if not validate_csrf_token(
            request, token, settings=app_settings, action="toggle", csrf_secret=csrf_secret
        ):
            return HTMLResponse(status_code=status.HTTP_403_FORBIDDEN, content="Forbidden")
        if strategy_name not in STRATEGY_REGISTRY:
            return HTMLResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content="Unknown strategy",
            )
        now = datetime.now(timezone.utc)
        connection = None
        try:
            connection = app.state.connect_postgres(app_settings.database_url)
            flag_store = _build_store(app.state.strategy_flag_store_factory, connection)
            audit_store = _build_store(app.state.audit_event_store_factory, connection)
            current_flag = flag_store.load(
                strategy_name=strategy_name,
                trading_mode=app_settings.trading_mode,
                strategy_version=app_settings.strategy_version,
            )
            new_enabled = not (current_flag.enabled if current_flag is not None else True)
            flag_store.save(
                StrategyFlag(
                    strategy_name=strategy_name,
                    trading_mode=app_settings.trading_mode,
                    strategy_version=app_settings.strategy_version,
                    enabled=new_enabled,
                    updated_at=now,
                ),
                commit=False,
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
                ),
                commit=False,
            )
            connection.commit()
        finally:
            close = getattr(connection, "close", None)
            if callable(close):
                close()
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/watchlist", response_class=HTMLResponse)
    def watchlist_page(request: Request) -> HTMLResponse:
        operator = current_operator(request, settings=app_settings)
        if auth_enabled(app_settings) and operator is None:
            return _render_login_page(app, request, next_path=request.url.path)
        connection = None
        try:
            connection = app.state.connect_postgres(app_settings.database_url)
            store = _build_store(app.state.watchlist_store_factory, connection)
            records = store.list_all(app_settings.trading_mode.value)
        finally:
            close = getattr(connection, "close", None)
            if callable(close):
                close()
        enabled_count = sum(1 for r in records if r.enabled)
        return templates.TemplateResponse(
            request=request,
            name="watchlist.html",
            context={
                "request": request,
                "trading_mode": app_settings.trading_mode.value,
                "strategy_version": app_settings.strategy_version,
                "operator_email": operator,
                "watchlist": records,
                "enabled_count": enabled_count,
                "error": request.query_params.get("error", ""),
            },
        )

    @app.post("/admin/watchlist/add")
    async def watchlist_add(request: Request) -> Response:
        operator = current_operator(request, settings=app_settings)
        if auth_enabled(app_settings) and operator is None:
            return RedirectResponse(url="/login?next=/watchlist", status_code=status.HTTP_303_SEE_OTHER)
        fields = await _read_form_fields(request)
        token = fields.get("_csrf_token", "")
        if not validate_csrf_token(
            request, token, settings=app_settings, action="watchlist", csrf_secret=csrf_secret
        ):
            return HTMLResponse(status_code=status.HTTP_403_FORBIDDEN, content="Forbidden")
        validated = _validate_symbol(fields.get("symbol", ""))
        if not validated:
            return RedirectResponse(url="/watchlist?error=invalid_symbol", status_code=status.HTTP_303_SEE_OTHER)
        connection = None
        try:
            connection = app.state.connect_postgres(app_settings.database_url)
            store = _build_store(app.state.watchlist_store_factory, connection)
            audit_store = _build_store(app.state.audit_event_store_factory, connection)
            now = datetime.now(timezone.utc)
            store.add(validated, app_settings.trading_mode.value, added_by=operator or "admin", commit=False)
            audit_store.append(
                AuditEvent(
                    event_type="WATCHLIST_ADD",
                    symbol=validated,
                    payload={"added_by": operator or "admin", "trading_mode": app_settings.trading_mode.value},
                    created_at=now,
                ),
                commit=False,
            )
            connection.commit()
        finally:
            close = getattr(connection, "close", None)
            if callable(close):
                close()
        return RedirectResponse(url="/watchlist", status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/admin/watchlist/remove")
    async def watchlist_remove(request: Request) -> Response:
        operator = current_operator(request, settings=app_settings)
        if auth_enabled(app_settings) and operator is None:
            return RedirectResponse(url="/login?next=/watchlist", status_code=status.HTTP_303_SEE_OTHER)
        fields = await _read_form_fields(request)
        token = fields.get("_csrf_token", "")
        if not validate_csrf_token(
            request, token, settings=app_settings, action="watchlist", csrf_secret=csrf_secret
        ):
            return HTMLResponse(status_code=status.HTTP_403_FORBIDDEN, content="Forbidden")
        symbol = fields.get("symbol", "").strip().upper()
        connection = None
        try:
            connection = app.state.connect_postgres(app_settings.database_url)
            store = _build_store(app.state.watchlist_store_factory, connection)
            audit_store = _build_store(app.state.audit_event_store_factory, connection)
            enabled = store.list_enabled(app_settings.trading_mode.value)
            if len(enabled) <= 1 and symbol in enabled:
                return RedirectResponse(url="/watchlist?error=last_symbol", status_code=status.HTTP_303_SEE_OTHER)
            now = datetime.now(timezone.utc)
            store.remove(symbol, app_settings.trading_mode.value, commit=False)
            audit_store.append(
                AuditEvent(
                    event_type="WATCHLIST_REMOVE",
                    symbol=symbol,
                    payload={"removed_by": operator or "admin", "trading_mode": app_settings.trading_mode.value},
                    created_at=now,
                ),
                commit=False,
            )
            connection.commit()
        finally:
            close = getattr(connection, "close", None)
            if callable(close):
                close()
        return RedirectResponse(url="/watchlist", status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/admin/watchlist/ignore")
    async def watchlist_ignore(request: Request) -> Response:
        operator = current_operator(request, settings=app_settings)
        if auth_enabled(app_settings) and operator is None:
            return RedirectResponse(url="/login?next=/watchlist", status_code=status.HTTP_303_SEE_OTHER)
        fields = await _read_form_fields(request)
        token = fields.get("_csrf_token", "")
        if not validate_csrf_token(
            request, token, settings=app_settings, action="watchlist", csrf_secret=csrf_secret
        ):
            return HTMLResponse(status_code=status.HTTP_403_FORBIDDEN, content="Forbidden")
        symbol = fields.get("symbol", "").strip().upper()
        connection = None
        try:
            connection = app.state.connect_postgres(app_settings.database_url)
            store = _build_store(app.state.watchlist_store_factory, connection)
            audit_store = _build_store(app.state.audit_event_store_factory, connection)
            now = datetime.now(timezone.utc)
            store.ignore(symbol, app_settings.trading_mode.value, commit=False)
            audit_store.append(
                AuditEvent(
                    event_type="WATCHLIST_IGNORE",
                    symbol=symbol,
                    payload={"ignored_by": operator or "admin", "trading_mode": app_settings.trading_mode.value},
                    created_at=now,
                ),
                commit=False,
            )
            connection.commit()
        finally:
            close = getattr(connection, "close", None)
            if callable(close):
                close()
        return RedirectResponse(url="/watchlist", status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/admin/watchlist/unignore")
    async def watchlist_unignore(request: Request) -> Response:
        operator = current_operator(request, settings=app_settings)
        if auth_enabled(app_settings) and operator is None:
            return RedirectResponse(url="/login?next=/watchlist", status_code=status.HTTP_303_SEE_OTHER)
        fields = await _read_form_fields(request)
        token = fields.get("_csrf_token", "")
        if not validate_csrf_token(
            request, token, settings=app_settings, action="watchlist", csrf_secret=csrf_secret
        ):
            return HTMLResponse(status_code=status.HTTP_403_FORBIDDEN, content="Forbidden")
        symbol = fields.get("symbol", "").strip().upper()
        connection = None
        try:
            connection = app.state.connect_postgres(app_settings.database_url)
            store = _build_store(app.state.watchlist_store_factory, connection)
            audit_store = _build_store(app.state.audit_event_store_factory, connection)
            now = datetime.now(timezone.utc)
            store.unignore(symbol, app_settings.trading_mode.value, commit=False)
            audit_store.append(
                AuditEvent(
                    event_type="WATCHLIST_UNIGNORE",
                    symbol=symbol,
                    payload={"unignored_by": operator or "admin", "trading_mode": app_settings.trading_mode.value},
                    created_at=now,
                ),
                commit=False,
            )
            connection.commit()
        finally:
            close = getattr(connection, "close", None)
            if callable(close):
                close()
        return RedirectResponse(url="/watchlist", status_code=status.HTTP_303_SEE_OTHER)

    return app


def _execute_admin_status_change(
    app: FastAPI,
    *,
    new_status: TradingStatusValue,
    command_name: str,
    kill_switch_enabled: bool,
    reason: str | None,
    operator: str | None,
    notifier: "Notifier",
) -> Response:
    app_settings = app.state.settings
    now = datetime.now(timezone.utc)
    connection = None
    try:
        connection = app.state.connect_postgres(app_settings.database_url)
        status_store = _build_store(app.state.trading_status_store_factory, connection)
        audit_store = _build_store(app.state.audit_event_store_factory, connection)
        ts = TradingStatus(
            trading_mode=app_settings.trading_mode,
            strategy_version=app_settings.strategy_version,
            status=new_status,
            kill_switch_enabled=kill_switch_enabled,
            status_reason=reason,
            updated_at=now,
        )
        status_store.save(ts, commit=False)
        payload: dict = {
            "command": command_name,
            "trading_mode": app_settings.trading_mode.value,
            "strategy_version": app_settings.strategy_version,
            "status": new_status.value,
            "operator": operator or "web",
        }
        if reason is not None:
            payload["reason"] = reason
        audit_store.append(
            AuditEvent(
                event_type="trading_status_changed",
                payload=payload,
                created_at=now,
            ),
            commit=False,
        )
        connection.commit()
        _subjects = {
            "halt": "Trading halted",
            "close-only": "Trading set to close-only",
            "resume": "Trading resumed",
        }
        try:
            notifier.send(
                subject=_subjects.get(command_name, f"Trading status changed: {command_name}"),
                body=(
                    f"mode={app_settings.trading_mode.value} "
                    f"strategy={app_settings.strategy_version} "
                    f"reason={reason or '-'} "
                    f"operator={operator or 'web'}"
                ),
            )
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Notifier send failed after status change")
    finally:
        close = getattr(connection, "close", None)
        if callable(close):
            close()
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)


def _parse_date_param(
    date_param: str,
    *,
    today: date,
) -> tuple[date, str | None]:
    if not date_param:
        return today, None
    try:
        parsed = date.fromisoformat(date_param)
    except ValueError:
        return today, f"Invalid date '{date_param}' — showing today's data."
    if parsed > today:
        return today, f"Date {parsed.isoformat()} is in the future — showing today's data."
    return parsed, None


def _isoformat(d: date | None) -> str | None:
    return d.isoformat() if d is not None else None


def _fetch_latest_prices(*, adapter: object | None, positions: list) -> dict[str, float]:
    if adapter is None or not positions:
        return {}
    symbols = list({p.symbol for p in positions})
    try:
        return adapter.get_latest_prices(symbols)  # type: ignore[union-attr]
    except Exception:
        import logging
        logging.getLogger(__name__).warning("Failed to fetch latest prices", exc_info=True)
        return {}


def _load_dashboard_data(app: FastAPI) -> tuple:
    connection = app.state.connect_postgres(app.state.settings.database_url)
    try:
        settings = app.state.settings
        order_store = _build_store(app.state.order_store_factory, connection)
        audit_event_store = _build_store(app.state.audit_event_store_factory, connection)
        position_store = _build_store(app.state.position_store_factory, connection)
        pre_positions = position_store.list_all(
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
        )
        latest_prices = _fetch_latest_prices(
            adapter=app.state.market_data_adapter,
            positions=pre_positions,
        )
        snapshot = load_dashboard_snapshot(
            settings=settings,
            connection=connection,
            trading_status_store=_build_store(app.state.trading_status_store_factory, connection),
            daily_session_state_store=_build_store(app.state.daily_session_state_store_factory, connection),
            position_store=position_store,
            order_store=order_store,
            audit_event_store=audit_event_store,
            strategy_flag_store=_build_store(app.state.strategy_flag_store_factory, connection),
            latest_prices=latest_prices,
        )
        metrics = load_metrics_snapshot(
            settings=settings,
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
            strategy_flag_store=_build_store(
                app.state.strategy_flag_store_factory,
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
    _s = app.state.settings
    return app.state.templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "request": request,
            "trading_mode": _s.trading_mode.value,
            "strategy_version": _s.strategy_version,
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
