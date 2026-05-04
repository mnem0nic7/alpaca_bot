# Security Fixes: Shell Injection and Credential Leakage

## Summary

Two security vulnerabilities identified in the security review of the `stop-order-reliability-fixes`
branch. Both are addressed in this spec.

---

## Vuln 1 — Shell Injection via `source "$ENV_FILE"` (HIGH)

### Context

`apply_candidate.sh` writes nightly parameter values from `candidate.env` into the system env file
(`/etc/alpaca_bot/alpaca-bot.env`) using `sed`. Three bash scripts then `source` that env file as root:
`scripts/deploy.sh`, `scripts/ops_check.sh`, `scripts/admin.sh`.

Because `source` evaluates the file as bash, a VALUE containing `$(cmd)` would execute that command
as root. The sed command uses `|` as a delimiter, so a `|`-containing value could also inject
additional sed commands.

### Root cause

`apply_candidate.sh` lines 37–44 (comparison loop) and 51–59 (apply loop) read `VALUE` from
`candidate.env` without validating its content before writing to the env file or embedding it in a
`sed` expression.

### Fix

Add a VALUE validation guard in `apply_candidate.sh` in BOTH loops. Any value that does not match
`^[A-Za-z0-9._+-]+$` is rejected and the script exits 1.

The allowlist covers:
- Integers: `20`, `14`
- Floats: `1.5`, `0.6`, `1.0`
- Version strings: `v1-breakout`
- Positive floats with explicit `+`: rare but defensive

The three tunable params written by the nightly pipeline (`BREAKOUT_LOOKBACK_BARS`,
`RELATIVE_VOLUME_THRESHOLD`, `DAILY_SMA_PERIOD`) all satisfy this pattern.

**We do NOT change `source` in deploy.sh/ops_check.sh/admin.sh.** Those scripts also use the sourced
variables for bash logic (`require_var`, `credentials_ready`). Switching them to `--env-file` would
require refactoring all that bash variable logic; the value-validation approach addresses the root
cause without touching three more scripts.

### Affected files

- `scripts/apply_candidate.sh` — add VALUE validation in both the comparison loop and the apply loop

### Test coverage

- Extend `tests/unit/test_apply_candidate.py` with a test that passes a `candidate.env` containing a
  value with a `$()` subshell and verifies the script exits non-zero without touching the env file.

---

## Vuln 2 — DB Credential Leakage via `/healthz` (MEDIUM)

### Context

The `/healthz` endpoint in `src/alpaca_bot/web/app.py` catches all exceptions and returns
`str(exc)` in the JSON body. psycopg connection failure exceptions include DB hostname, port, and
username. This endpoint is unauthenticated (intentionally, for uptime probes).

### Fix

Replace `str(exc)` with a static opaque string. Log the exception server-side using the module
logger so the connection details remain visible in server logs but not in HTTP responses.

```python
except Exception as exc:
    import logging
    logging.getLogger(__name__).exception("/healthz: health check failed")
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "error", "reason": "service unavailable"},
    )
```

### Affected files

- `src/alpaca_bot/web/app.py` — healthz exception handler (lines 263–267)

### Test coverage

The existing test `test_healthz_route_returns_503_when_database_fails` (line 241,
`tests/unit/test_web_app.py`) currently asserts `{"reason": "db unavailable"}`. It must be updated
to assert `{"reason": "service unavailable"}`. Add a second assertion that the raw exception message
is NOT in the response body (defense regression test).

---

## Non-changes

- `scripts/deploy.sh`, `scripts/ops_check.sh`, `scripts/admin.sh`: `source "$ENV_FILE"` is
  intentionally kept. The value-validation fix in `apply_candidate.sh` is the correct defense layer.
- Database migrations: none required.
- `Settings`: no new env vars.
- Trading engine: `evaluate_cycle()` is untouched.
- Paper/live safety: unaffected — no order path changes.
