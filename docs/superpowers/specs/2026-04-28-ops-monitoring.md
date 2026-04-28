# Spec: Ops/Monitoring Improvements

## Problem

Two classes of operational events currently go unnotified:

1. **Web UI admin actions** (halt/resume/close-only via dashboard) write a `trading_status_changed`
   audit event and update the DB but never call the notifier. The CLI `halt` command does notify,
   creating an inconsistency: the same action via the web is silent.

2. **Admin CLI close-only and resume** also don't notify — only `halt` does.

Additionally, `/healthz` doesn't expose per-strategy enabled state, making it harder for external
monitors to determine which strategies are active.

## Goals

1. All trading-status changes (halt / close-only / resume) trigger a notification regardless of
   whether they originate from the CLI or the web UI.
2. `/healthz` returns per-strategy flag state so external monitors can detect misconfigured flags.
3. No new env vars, no new infrastructure — reuse the existing `Notifier` / `build_notifier`
   pipeline.

## Non-Goals

- Worker-staleness push alerts (staleness is a pull-based concept; the supervisor can't know it's
  stale). External uptime monitors hit `/healthz` for this.
- Slippage notifications (`notify_slippage_threshold_pct` is already validated; wiring it up is
  a separate feature).
- Dashboard UI changes beyond what `/healthz` already exposes.

## Design

### 1. Notifier in `create_app()`

`create_app()` gains an optional `notifier: Notifier | None = None` parameter. When None, it calls
`build_notifier(app_settings)` internally — the same factory the supervisor uses. The notifier is
stored on `app.state.notifier` so route helpers can reach it.

Tests inject a `FakeNotifier` (captures sent messages) via this parameter, identical to how the
supervisor tests inject fake callables.

### 2. Admin route notifications

`_execute_admin_status_change()` gains a `notifier` parameter. After `connection.commit()`, it calls
`notifier.send(subject, body)`. Subjects follow the existing CLI convention:
- halt → "Trading halted"
- close-only → "Trading set to close-only"
- resume → "Trading resumed"

Body: `mode=<value> strategy=<version> reason=<reason or '-'> operator=<email or web>`

Notifier failures are caught and logged (never abort the redirect).

### 3. Admin CLI close-only and resume notifications

`run_admin_command()` and `main()` in `admin/cli.py` already accept a `notifier` parameter. Add
`notifier.send()` calls for `close-only` and `resume` after the status change, matching the
existing `halt` pattern.

### 4. Enhanced `/healthz`

Add `strategy_flags` list to the response:
```json
{
  "strategy_flags": [
    {"name": "breakout", "enabled": true},
    {"name": "momentum", "enabled": false}
  ]
}
```

This requires `load_health_snapshot()` to also load strategy flags. `HealthSnapshot` gains a
`strategy_flags: list[tuple[str, bool]]` field. The `/healthz` handler serialises it to the list
of dicts above.

HTTP status remains 200/503 based on worker freshness only — strategy flag state doesn't change
the HTTP status code.

## Files Changed

| File | Change |
|------|--------|
| `src/alpaca_bot/web/app.py` | Add `notifier` param to `create_app()` + `_execute_admin_status_change()`; build notifier from settings when None; call notifier after commit |
| `src/alpaca_bot/web/service.py` | Add `strategy_flags` to `HealthSnapshot`; update `load_health_snapshot()` to load flags |
| `src/alpaca_bot/admin/cli.py` | Add notifier calls for close-only and resume in `run_admin_command()` and `main()` |
| `tests/unit/test_web_service.py` | Tests for notifier calls and enhanced healthz |
| `tests/unit/test_admin_cli.py` | Tests for close-only/resume notifications |

## Safety Analysis

- No order submission, position sizing, or broker calls — purely informational path.
- Notifier failures are caught and never abort the DB write or redirect.
- No new env vars (notifier already configured via `SLACK_WEBHOOK_URL` / `NOTIFY_SMTP_*`).
- `ENABLE_LIVE_TRADING=false` gate is unaffected.
- No migration needed.
