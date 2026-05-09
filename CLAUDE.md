# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install in editable mode (required before running anything)
pip install -e ".[dev]"

# Run all tests
pytest

# Run a single test file
pytest tests/unit/test_cycle_engine.py

# Run a single test by name
pytest tests/unit/test_cycle_engine.py::test_name -v

# Apply database migrations
alpaca-bot-migrate

# Start the supervisor (long-running worker)
alpaca-bot-supervisor

# Start the dashboard (read-only FastAPI, port 18080)
alpaca-bot-web

# Admin commands (halt/resume/status)
alpaca-bot-admin status
alpaca-bot-admin halt --reason "..."
alpaca-bot-admin resume --reason "..."
alpaca-bot-admin close-only --reason "..."

# Generate dashboard password hash
alpaca-bot-web-hash-password

# Docker deploy (uses deploy/compose.yaml)
./scripts/deploy.sh /etc/alpaca_bot/alpaca-bot.env
```

The app reads config exclusively from environment variables — there is no `.env` autoload. See `DEPLOYMENT.md` for a complete env file template.

## Production Environment

**This workspace (`/workspace/alpaca_bot`) IS the production server.** Deploy by running the script directly — no SSH needed.

```bash
# Rotate dashboard password (interactive — run in terminal with !)
! docker run --rm -it alpaca-bot:latest alpaca-bot-web-hash-password
# Then update DASHBOARD_AUTH_PASSWORD_HASH in /etc/alpaca_bot/alpaca-bot.env and redeploy
```

**Credential name mismatch:** The project-root `.env` uses `ALPACA_PAPER_KEY` / `ALPACA_PAPER_SECRET`, but the system env file and `Settings` expect `ALPACA_PAPER_API_KEY` / `ALPACA_PAPER_SECRET_KEY`. When syncing, map the names explicitly.

**Alpaca API reference:** Repo-local notes from the current Alpaca docs live in `docs/ALPACA_API.md`. Read that before changing `execution/`, `runtime/order_dispatch.py`, `runtime/cycle_intent_execution.py`, option-chain logic, or market data feed behavior.

**Reverse proxy:** `campaign_tracker-caddy-1` handles TLS and routing. `alpaca.ai-al.site` → `web:8080`. Caddy config lives inside that container (`/etc/caddy/Caddyfile`).

**GitHub Actions deploy** (`deploy.yml`) requires `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY` secrets — without them it fails at the SSH step. Since this is the prod server, prefer running `./scripts/deploy.sh` directly.

## Parallel Subagents

**Always dispatch independent work as parallel subagents.** Do not run sequential agents when tasks have no data dependency between them.

Examples of work to parallelize:
- Planner + Reviewer launched together on separate concerns
- Test run + lint/type-check in parallel
- Multiple file explorations or grep searches
- Code review + security audit after a commit

Use a single message with multiple `Agent` tool calls. Sequential execution is only justified when output of one agent is required input for the next.

## Architecture

### Layers

```
config/          → Settings (frozen dataclass, parsed from env at startup)
domain/          → Pure data types: Bar, OpenPosition, BreakoutSignal, CycleIntent
strategy/        → Stateless signal logic: breakout detection, trend filter, session time guards
risk/            → Position sizing math
core/engine.py   → evaluate_cycle(): pure function, no I/O, produces CycleResult (list of intents)
execution/       → Alpaca API adapters (broker, market data, trade stream)
storage/         → Postgres repositories, advisory lock, migrations, audit events
runtime/         → Orchestration: supervisor loop, order dispatch, startup recovery, trade update stream
admin/           → CLI tools for operator control (halt, resume, ops check, credential sync)
web/             → FastAPI read-only dashboard (/healthz, HTML overview)
replay/          → Offline scenario runner for strategy testing
```

### Key design patterns

**`Settings` is the spine.** `Settings.from_env()` is called once; the frozen dataclass is threaded through every function call. Strategy, risk, and session-time logic all read from it rather than from globals.

**`evaluate_cycle()` is a pure function.** `core/engine.py` takes market data and open positions and returns `CycleResult` (a list of `CycleIntent` objects). No I/O, no side effects — makes it easy to test and replay.

**Dependency injection via callables.** `RuntimeSupervisor` accepts optional `Callable` overrides for `cycle_runner`, `order_dispatcher`, `cycle_intent_executor`, etc. Tests pass lightweight fakes; production uses the real implementations as defaults.

**Intent → order dispatch separation.** The cycle emits intents (`entry`, `update_stop`, `exit`); a separate `order_dispatch` step converts pending-submit orders in Postgres to actual Alpaca API calls. This two-phase design means intents survive a crash and can be dispatched on the next cycle.

**Postgres advisory lock.** `bootstrap_runtime()` acquires a per-(trading_mode, strategy_version) advisory lock at startup, preventing two supervisor instances from running simultaneously.

**Audit log over direct state.** The supervisor appends `AuditEvent` rows for every significant action (cycle run, reconciliation, stream start/stop). Admin and dashboard reads derive status from these events.

### Deployed services (Docker Compose)

| Service | Entrypoint | Role |
|---|---|---|
| `supervisor` | `alpaca-bot-supervisor` | Long-running trading loop, 60s poll |
| `web` | `alpaca-bot-web` | Read-only FastAPI dashboard on `127.0.0.1:18080` |
| `migrate` | `alpaca-bot-migrate` | One-shot migration runner at deploy time |
| `postgres` | postgres image | Local state store |

### Trading flow (per cycle)

1. `RuntimeSupervisor.run_cycle_once()` fetches account equity and intraday/daily bars from Alpaca.
2. `evaluate_cycle()` (pure) produces `CycleIntent` objects for entries, stop updates, and exits.
3. `run_cycle()` writes ENTRY intents as `pending_submit` orders to Postgres; `execute_cycle_intents()` handles UPDATE_STOP and EXIT intents by calling the broker directly.
4. `dispatch_pending_orders()` submits pending-submit entry orders to the Alpaca API and updates their status.
5. A background thread runs the Alpaca trade update WebSocket stream; fills update position records.

### Testing conventions

All tests are under `tests/unit/`. Tests use dependency injection (fake callables, in-memory stores) rather than mocking. The `pytest.ini` sets `pythonpath = src` so imports resolve without installation, but `pip install -e .` is still needed for the CLI entry points.

`ENTRY_TIMEFRAME_MINUTES` is hardcoded to 15 in `Settings.validate()` — the strategy is coupled to 15-minute bars.

## Agent Team

**All requests — features, fixes, questions about design, refactors, and investigations — are handled through the agent team workflow below. Do not write, modify, or delete code outside of this workflow. When in doubt, start with `/plan-and-refine`.**

**Agents must continue working autonomously as long as there is ready work or ready work can be generated from completed work.** Do not stop and wait for user confirmation between steps. Only pause when all available work is blocked on an external decision that cannot be resolved from the codebase.

Six roles cover all development work on this project. Planner and Feature Developer require explicit human invocation. The others have automatic triggers via hooks.

### Roster

| Role | Skill | Trigger |
|---|---|---|
| **Tester** | `/tdd` | Manual before any code change; always run `pytest` before committing |
| **Reviewer** | `/code-review:code-review` | Automatic — fires when Claude finishes writing code (Stop hook) |
| **Security Auditor** | `/security-review` | Automatic — fires when Claude finishes a task touching `execution/`, `config/`, or `admin/` |
| **Completionist** | `/superpowers:verification-before-completion` | Automatic — fires before any task is declared done (Stop hook) |
| **Planner** | `/plan-and-refine` | Explicit invocation only — ALWAYS use before scoping any new feature or fix |
| **Feature Developer** | `/feature-dev:feature-dev` | Explicit invocation only — run only after Planner produces a grilled, refined plan |
| **Deploy Reviewer** | manual checklist | Run when any file under `deploy/`, `scripts/`, or `Dockerfile` changes — verify migrate runs before supervisor, ENABLE_LIVE_TRADING cannot be true without TRADING_MODE=live, no new host-facing ports without docs |

### Planning mandate

**Every request — feature, fix, refactor, investigation, or design question — goes through `/plan-and-refine` before any code is written or any advice is given.** No exceptions. If the request feels too small to plan, it isn't.

`/plan-and-refine` runs the full loop:
1. `superpowers:brainstorming` — explore intent and constraints, produce a committed spec
2. `superpowers:writing-plans` — produce a step-by-step plan with exact file paths and code
3. `grill-me` — adversarial interrogation of the plan until every branch of the design tree is resolved. **The agent must answer every grill-me question itself** using codebase knowledge and reasoning — do not surface unanswered questions to the user.
4. Refine — update the plan based on grilling; re-grill if tasks changed
5. Hand off to `feature-dev:feature-dev`

The Planner no longer invokes brainstorming and write-plan separately. `/plan-and-refine` is the single entry point.

**The plan-and-refine → feature-dev pipeline is fully autonomous.** The agent runs all stages (brainstorm → write-plan → grill → refine → feature-dev) without stopping for user approval between steps. Every grill-me question is answered by the agent using the codebase — never delegated to the user. The only valid pause point is a genuine external dependency (missing credentials, blocked API access) that cannot be resolved from within the repo.

### Mandates

**Tester:** Owns test coverage. All new logic in `core/`, `strategy/`, `risk/`, and `runtime/` must have a corresponding unit test using the project's DI pattern (fake callables, in-memory stores — no mocks). Run `pytest` before every commit. Consult the `/tdd` skill's companion docs for boundaries: `mocking.md` (never mock your own classes — use fakes instead), `tests.md` (test quality), `deep-modules.md` (module depth). The project's fake-callables pattern is the DI-at-system-boundaries approach described in `mocking.md`.

**Reviewer:** Reads the diff after Claude writes code. Flags logic errors in financial calculations, violations of the intent/dispatch separation, and any state mutation that bypasses the audit log.

**Security Auditor:** Scans changes to `execution/` (Alpaca API calls), `config/` (Settings, credential parsing), and `admin/` (CLI operator tools). Looks for credential leakage, injection risks, and auth bypass.

**Completionist:** Before closing any task, verifies the implementation matches the original intent, tests pass, no TODOs were left in, and the audit trail is intact for any runtime change. Also owns CLAUDE.md: after any session that adds a skill, changes a hook, or modifies the agent roster, update CLAUDE.md to reflect the actual state.

**Note on hooks:** The Stop and PostToolUse hooks in `.claude/settings.json` are advisory reminders — they print checklists to stdout but do not auto-invoke skills. Treat them as prompts requiring manual invocation of the listed skills.

**Context compaction:** `.claude/settings.json` sets `autoCompactWindow: 150000` — context is automatically compacted when it approaches 150k tokens. Run `/compact` manually at any time to compact sooner.

**Planner:** Runs `/plan-and-refine` — the full brainstorm → write-plan → grill-me → refine loop. Does not hand off to Feature Developer until at least one full grilling round produces no plan changes. All grill-me questions are answered by the agent, not the user — use codebase knowledge, architecture docs, and first-principles reasoning to resolve every open question before refining.

**Feature Developer:** Executes a written plan from the Planner using `/feature-dev:feature-dev`. Does not scope or design — consumes the grilled, refined plan as input.
