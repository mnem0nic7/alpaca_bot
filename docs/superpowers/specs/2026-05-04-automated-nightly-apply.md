# Spec: Automated Nightly Parameter Apply

**Date:** 2026-05-04
**Branch:** stop-order-reliability-fixes

---

## Problem

`alpaca-bot-nightly` runs at 22:30 UTC every weekday, trains on the day's data, and writes a
`candidate.env` file with the 3 best-found parameters to a Docker-managed named volume
(`nightly_data`). But nothing reads that file back automatically. To apply the new parameters to
tomorrow's trading session, an operator must:

1. Run a container to extract `candidate.env` from the named volume
2. Manually update `/etc/alpaca_bot/alpaca-bot.env`
3. Run `./scripts/deploy.sh` to restart the supervisor

This manual step is required for every winning training run. Without it, the supervisor continues
trading with yesterday's parameters regardless of what tonight's training found.

---

## Fix

Three targeted changes:

**1. Replace named volume with bind mount.**
Switch the `nightly` service from `nightly_data:/data` (Docker-managed named volume) to
`/var/lib/alpaca-bot/nightly:/data` (host bind mount). This makes `candidate.env` directly readable
by host-side scripts without running a container.

**2. Add `scripts/apply_candidate.sh`.**
A script that:
- Reads `candidate.env` from the bind-mount path
- Compares each param against the current value in the system env file
- Updates only changed params via `sed -i`
- If any param changed, calls `./scripts/deploy.sh` to restart the supervisor
- Exits 0 (no-op) if no `candidate.env` found, or all params already current

Accepts three positional args for testability:
- `$1` — `ENV_FILE` (default `/etc/alpaca_bot/alpaca-bot.env`)
- `$2` — `CANDIDATE_ENV` (default `/var/lib/alpaca-bot/nightly/candidate.env`)
- `$3` — `DEPLOY_SCRIPT` (default `$ROOT_DIR/scripts/deploy.sh`)

**3. Extend the host cron.**
Chain `apply_candidate.sh` after the nightly run using `&&`:
```
30 22 * * 1-5 root cd /workspace/alpaca_bot && docker compose -f deploy/compose.yaml run --rm nightly >> /var/log/alpaca-bot-nightly.log 2>&1 && ./scripts/apply_candidate.sh /etc/alpaca_bot/alpaca-bot.env >> /var/log/alpaca-bot-nightly.log 2>&1
```
`&&` ensures apply only runs if nightly exits 0. If nightly found no winner, `candidate.env` is not
updated by the nightly run, so apply is a no-op (params unchanged → no restart).

---

## Files Changed

| File | Change |
|---|---|
| `deploy/compose.yaml` | Replace `nightly_data:/data` with `/var/lib/alpaca-bot/nightly:/data`; remove `nightly_data` from top-level `volumes:` block |
| `scripts/apply_candidate.sh` | New — merge params, restart supervisor |
| `scripts/init_server.sh` | Add `mkdir -p /var/lib/alpaca-bot/nightly` |
| `deploy/cron.d/alpaca-bot` | Chain `apply_candidate.sh` after nightly with `&&` |
| `tests/unit/test_apply_candidate.py` | New — 4 subprocess tests |
| `DEPLOYMENT.md` | Add "Automated Nightly Apply" section |

No migrations. No new env vars. No changes to `evaluate_cycle()`, order dispatch, or any live-trading
path.

---

## Safety Analysis

**Financial safety:** The apply script changes env vars and restarts the supervisor. No order
submission, no position sizing, no stop placement in the script itself. The `credentials_ready()`
check in `deploy.sh` prevents the supervisor from starting if credentials are missing or
`ENABLE_LIVE_TRADING` is not set correctly. Live trading safety gates are preserved.

**Param scope:** Only the 3 sweep grid parameters are changed (`BREAKOUT_LOOKBACK_BARS`,
`RELATIVE_VOLUME_THRESHOLD`, `DAILY_SMA_PERIOD`). All risk, timing, and credential parameters are
untouched.

**Idempotency:** If params are already current (e.g., same winner as last night), apply detects no
change and skips the restart. Supervisor uptime is not disrupted unnecessarily.

**No-winner case:** If nightly found no held candidate, it does not write to `candidate.env`. The
file on disk reflects the last successful training run. Apply compares its params against the env
file; if they already match (from last night's apply), it exits 0 with no restart.

**Market hours:** Apply fires at or after 22:30 UTC (well after NYSE closes at 20:00 UTC). The
supervisor restart completes before pre-market opens at 13:00 UTC next day. No trading is disrupted.

**Advisory lock:** Supervisor restart releases and re-acquires the Postgres advisory lock. A second
supervisor cannot start if one is already running. Docker `up -d --force-recreate` stops the old
container cleanly before starting the new one.

**Audit trail:** The apply script is an operator-level action equivalent to running `deploy.sh`
manually. No `AuditEvent` rows needed beyond what supervisor bootstrap already logs on startup.

**Rollback:** If the new params perform poorly, the operator can revert by editing the env file and
re-running `deploy.sh`. The DB `tuning_results` table retains all historical runs for reference.

**Volume migration:** On first run after this change, the bind mount path (`/var/lib/alpaca-bot/nightly`)
is empty. The nightly service will re-fetch scenario files. The old `nightly_data` Docker volume is
not deleted automatically; operator can clean up with `docker volume rm alpaca_bot_nightly_data`.

**Pure engine boundary:** `evaluate_cycle()` is untouched.

**Paper vs. live:** `credentials_ready()` in `deploy.sh` is unchanged. `ENABLE_LIVE_TRADING=false`
remains an effective gate.

---

## Design Decisions

**Bind mount over named volume.** Named volumes require a container to access their contents. A bind
mount at a well-known host path makes `candidate.env` directly readable by host scripts. The trade-off
is that the operator must ensure `/var/lib/alpaca-bot/nightly` exists — handled by `init_server.sh`.

**No candidate.env timestamp check.** The apply script does not verify that `candidate.env` was
written "today." If nightly found no winner, it doesn't overwrite the file. The stale-file case
(Monday winner, Tuesday no winner, Wednesday apply) applies Monday's params again — idempotently,
with no restart if they're already current.

**`&&` chaining over a separate cron line.** A separate 22:45 line would fire even if nightly failed
or found no winner and ran for longer than 15 minutes. `&&` chaining ties apply to nightly's exit 0,
which is the correct behavior. No race condition is possible — apply never starts until nightly exits.

**Three-arg script interface.** Accepting `DEPLOY_SCRIPT` as a third positional arg makes the apply
script testable without modifying the real `deploy.sh`. Production callers pass no third arg
(defaults to the real script). Tests pass a mock.
