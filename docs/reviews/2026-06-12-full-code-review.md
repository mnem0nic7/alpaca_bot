# Full code + security review — trading-critical paths (2026-06-12)

**Scope:**
- Correctness: `core/engine.py`, `risk/` (all), `runtime/order_dispatch.py`, `runtime/cycle_intent_execution.py`, `replay/` (all, post harness fix e33eb87/fe809c0).
- Security: `execution/`, `config/`, `admin/`, plus `web/` auth handling and storage SQL parameterization.

**Methodology:** two parallel review subagents (correctness via `feature-dev:code-reviewer`, security via a read-only `general-purpose` agent), per the project Reviewer and Security Auditor mandates. Findings were confidence-filtered by the agents; secrets in `/etc/alpaca_bot/alpaca-bot.env` were not read or quoted.

## Findings (severity-ranked)

| # | Severity | Area | Location | Description | Recommended action |
|---|---|---|---|---|---|
| C1 | High | Replay stop tracking | `replay/runner.py:376` | `_handle_stop_update` applies a stop only if `intent_stop > position.stop_price` — always false for short trailing stops, so short stop tightening is silently dropped. Inert today (replay only enters longs) but wrong for any future short-equity replay. | Direction-aware comparison keyed on `position.quantity < 0`. |
| C2 | High | Broker state integrity | `runtime/cycle_intent_execution.py:826-843` | Options market-hours guard returns `(0, 0, 0)` after broker stop cancellations but before `canceled_order_records` are persisted — DB would still show those stops as active. Practically inert (short-option positions carry `stop_price == 0.0`, so no stops exist to cancel), but a silent trap for any future stop on a short option. | Move the guard before the cancellation section, or persist cancellations before the early return. |
| S1 | Medium | Dashboard auth | `web/app.py` (mutating routes), `config/__init__.py:93` | Auth fails open: `DASHBOARD_AUTH_ENABLED` defaults to `false` and every admin mutation route (halt/resume/close-only/toggles/watchlist) is gated by `auth_enabled(...) and operator is None`. Caddy proxies the dashboard publicly, so a missing env var exposes resume-after-halt to the internet. CSRF degrades to a constant key when auth is off. | Fail closed: refuse mutating routes (or refuse to start) when auth is disabled, or default the flag to true. |
| S2 | Medium | Session signing | `web/auth.py:57-61, 188-193, 212` | Session-token HMAC and CSRF secret are keyed on the stored scrypt password *hash* (or static `b"no-auth"` when unset) — anyone who can read the env var can forge sessions without knowing the password. | Dedicated random `DASHBOARD_SESSION_SECRET`; refuse auth rather than falling back to a constant key. |
| C3 | Medium | Replay reporting | `replay/runner.py:139-143` | All engine EXIT intents (eod_flatten, viability_trend_filter_failed, viability_vwap_breakdown, stop_breach_extended_hours) route to `_handle_eod_exit` and are recorded as `EOD_EXIT` — exit-reason breakdowns (eod vs stop) in backtest reports are distorted. P&L amounts unaffected. | Thread `intent.reason` into the event so reports can classify exits correctly. |
| C4 | Medium | Position sizing style | `risk/sizing.py:39-44` | After the `max_notional` cap, the explicit `if not fractionable and quantity < 1: return 0.0` guard present on the other cap paths is missing; behavior is currently correct only because of the trailing `max(float(quantity), 0.0)` plus the engine's `quantity <= 0` reject. | Add the symmetric guard after the floor. |
| S3 | Low | Credential file perms | `admin/credential_sync.py:65,76` | Env-file rewrite preserves existing permissions (`or 0o600` only fires on mode 0); a pre-existing world-readable env file stays world-readable after API keys are written into it. Atomic tempfile + `os.replace` pattern itself is correct. | Enforce `0o600` unconditionally. |
| S4 | Low | Open redirect | `web/app.py:1098-1101` | `_local_path` rejects `//` but not `/\`; browsers normalize `Location: /\evil.com` to a protocol-relative redirect after login. | Also reject paths containing `\`. |
| S5 | Low | Brute force | `web/app.py` login + Basic auth | No rate limiting/lockout and no AuditEvent on failed logins; online brute force throttled only by scrypt cost. | Add failure auditing and basic rate limiting. |
| S6 | Low | SSRF surface | `admin/ops_check.py` | `--url` is passed to `urlopen` with no scheme allowlist (`file://` accepted). Operator-only CLI, low risk. | Allowlist `http`/`https`. |
| S7 | Low | Secret handling | `web/password_rotation.py:78-80` | Rotated plaintext password printed to stdout (can land in CI/terminal logs); `--password` argv visible in process list. File itself is written `0o600`. | Prefer `getpass` prompting; document the stdout behavior. |
| S8 | Info | Env-file quoting | `admin/credential_sync.py:49` | `shlex.quote` may wrap values in single quotes that docker-compose `env_file` parsing treats literally — credential-corruption (functional) risk, not a vulnerability. | Validate round-trip or use compose-safe escaping. |
| S9 | Info | Config defaults | `config/__init__.py` filter flags | Dataclass field defaults (`True`) for `enable_regime_filter`/`enable_news_filter`/`enable_spread_filter` diverge from `from_env` defaults (`"false"`); dead for env-built instances, misleading for direct construction. | Align the defaults. |

## Areas verified clean

**Correctness:** engine regime/trend window anchoring (excludes current partial bar; no look-ahead); trailing/breakeven stop direction logic for long and short; stop-cap pass double-update guard; entry ranking; Wilder ATR (seed + smoothing, `period+1` minimum); sizing cascade; option contract sizing; order-dispatch stale-order expiry (atomic with audit events); intent/dispatch separation intact (no path executes an ENTRY intent directly); cycle-intent exit re-verification (prevents naked shorts); post-fix replay runner (symbols override, strict `< session day` daily slice, gap-open fill simulation, long P&L arithmetic); bootstrap CI/p-value math; report Sharpe/drawdown computation.

**Security:** `execution/` raises on missing credentials listing only variable *names*; no subprocess/eval/shell anywhere in scope; `_parse_bool` raises on unrecognized values (no truthy-string bypass of `ENABLE_LIVE_TRADING`); `Settings.validate()` enforces live-mode coupling in both directions, and a misspelled `ENABLE_LIVE_TRADING` falls back to `false` (the safe direction); all secret fields are `repr=False`; admin CLI audit payloads carry no credentials; all SQL uses bound parameters (f-strings interpolate only constant column lists); scrypt parameters capped against hash-parameter DoS; timing-safe comparisons throughout; session cookies `httponly`/`secure`/`samesite=lax`; watchlist symbols validated against `^[A-Z]{1,5}$`.

## Disposition

Per the plan, findings are **not** fixed ad hoc in this cycle. Queued as their own `/plan-and-refine` cycles (high severity first):

1. **C1 + C3** — replay exit/stop fidelity (direction-aware stop updates, exit-reason threading). One cycle: same file, same simulation concern.
2. **C2** — persist stop cancellations before the options market-hours early return.
3. **S1 + S2** — dashboard fail-closed auth + dedicated session secret. One cycle: both change the web auth trust model and should be designed together.

Medium/low items (C4, S3–S7) are candidates to batch into a hardening cycle after the above; S8/S9 are informational.
