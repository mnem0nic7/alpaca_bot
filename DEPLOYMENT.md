# alpaca_bot Operator Deployment

This repo currently deploys as a self-hosted Docker stack on one server: local `postgres`, a long-running worker container `alpaca-bot-supervisor`, and a read-only local dashboard `alpaca-bot-web`. Operational control remains CLI-only through `alpaca-bot-admin`.

## Runtime shape

- `alpaca-bot-supervisor` is the server process to keep running.
- `alpaca-bot-web` is a read-only FastAPI dashboard bound to `127.0.0.1:18080`.
- The dashboard supports optional HTTP Basic auth using a single pre-provisioned operator account from env.
- `postgres` is the local state store for orders, positions, audit events, and status.
- `alpaca-bot-migrate` applies SQL migrations in `migrations/`.
- `alpaca-bot-admin` is for operator actions such as `status`, `halt`, `close-only`, and `resume`.
- `alpaca-bot-ops-check` validates deployed health from the Docker network against `/healthz`.
- `alpaca-bot-sync-credentials` updates the server env file from CI-provided Alpaca secrets.
- `alpaca-bot-trader` exists as a one-shot startup/reconciliation entrypoint, but it is not the primary deployed service for the current runtime.

## Recommended server layout

Use any equivalent layout you prefer, but keep the environment file outside the repo checkout.

- Repo checkout: `/srv/alpaca_bot/current`
- Environment file: `/etc/alpaca_bot/alpaca-bot.env`

The application reads settings from process environment variables. It does not load a repo-local `.env` file on its own, so your deploy tooling must load `/etc/alpaca_bot/alpaca-bot.env` before running commands. The checked-in Docker Compose stack lives at `deploy/compose.yaml`, and the checked-in helper scripts are `scripts/init_server.sh`, `scripts/deploy.sh`, and `scripts/admin.sh`.

## Environment file

Example `/etc/alpaca_bot/alpaca-bot.env`:

```dotenv
TRADING_MODE=paper
ENABLE_LIVE_TRADING=false
STRATEGY_VERSION=v1-breakout
POSTGRES_DB=alpaca_bot
POSTGRES_USER=alpaca_bot
POSTGRES_PASSWORD=replace-me
DATABASE_URL=postgresql://alpaca_bot:replace-me@postgres:5432/alpaca_bot
MARKET_DATA_FEED=iex  # use "sip" only with a paid Alpaca subscription; paper accounts require "iex"
SYMBOLS=AAPL,MSFT,SPY

DAILY_SMA_PERIOD=20
BREAKOUT_LOOKBACK_BARS=20
RELATIVE_VOLUME_LOOKBACK_BARS=20
RELATIVE_VOLUME_THRESHOLD=1.5
ENTRY_TIMEFRAME_MINUTES=15
RISK_PER_TRADE_PCT=0.0025
# Per-trade dollar loss cap: limit how much a single stopped-out trade can lose in absolute
# dollar terms. When set, position size is reduced so that a clean stop-out loses at most
# this amount. Composable with RISK_PER_TRADE_PCT — the tighter constraint wins.
# (unset = disabled; recommended starting value for a ~$10K account: 12)
# MAX_LOSS_PER_TRADE_DOLLARS=12
MAX_POSITION_PCT=0.015
MAX_OPEN_POSITIONS=20
MAX_PORTFOLIO_EXPOSURE_PCT=0.30
DAILY_LOSS_LIMIT_PCT=0.01
# Intra-day review: send a performance digest every N REGULAR cycles (0 = disabled)
# At the default 60s poll, INTRADAY_DIGEST_INTERVAL_CYCLES=60 sends roughly hourly.
INTRADAY_DIGEST_INTERVAL_CYCLES=0
# Disable entries after N consecutive losing trades (0 = disabled, safe default)
INTRADAY_CONSECUTIVE_LOSS_GATE=0
# Extended-hours trading (pre-market 4am–9:20am ET and after-hours 4:05pm–7:30pm ET)
# EXTENDED_HOURS_ENABLED=false
# PRE_MARKET_ENTRY_WINDOW_START=04:00
# PRE_MARKET_ENTRY_WINDOW_END=09:20
# AFTER_HOURS_ENTRY_WINDOW_START=16:05
# AFTER_HOURS_ENTRY_WINDOW_END=19:30
# EXTENDED_HOURS_FLATTEN_TIME=19:45
# EXTENDED_HOURS_LIMIT_OFFSET_PCT=0.001   # limit price slippage buffer vs. last trade price
# EXTENDED_HOURS_MAX_SPREAD_PCT=0.01      # max bid-ask spread as fraction of price (1% default)
STOP_LIMIT_BUFFER_PCT=0.001
BREAKOUT_STOP_BUFFER_PCT=0.001
ATR_PERIOD=14
ATR_STOP_MULTIPLIER=1.0
ENTRY_STOP_PRICE_BUFFER=0.01
ENTRY_WINDOW_START=10:00
ENTRY_WINDOW_END=15:30
FLATTEN_TIME=15:45

# Options trading (disabled by default; set ENABLE_OPTIONS_TRADING=true to activate)
# ENABLE_OPTIONS_TRADING=false
# OPTION_DTE_MIN=21        # minimum days-to-expiry when selecting contracts
# OPTION_DTE_MAX=60        # maximum days-to-expiry when selecting contracts
# OPTION_DELTA_TARGET=0.50 # target delta for contract selection (0 < value ≤ 1.0)
#
# Bear (put) strategies are activated alongside ENABLE_OPTIONS_TRADING. When enabled,
# all 11 bearish strategies run in parallel with the existing breakout_calls strategy.
# Each strategy uses the same put-contract selector (DTE/delta settings above).
# To trade inverse ETFs instead of options, disable ENABLE_OPTIONS_TRADING and add
# the inverse ETF ticker (e.g. SQQQ, SPXS) to SYMBOLS — the bearish equity signals
# will then fire on those tickers directly without requiring an options chain.

ALPACA_PAPER_API_KEY=replace-me
ALPACA_PAPER_SECRET_KEY=replace-me
# For live mode, set:
# ALPACA_LIVE_API_KEY=replace-me
# ALPACA_LIVE_SECRET_KEY=replace-me

DASHBOARD_AUTH_ENABLED=true
DASHBOARD_AUTH_USERNAME=operator@example.com
DASHBOARD_AUTH_PASSWORD_HASH='replace-me'
```

Notes:

- `TRADING_MODE=paper` requires paper credentials.
- `TRADING_MODE=live` requires `ENABLE_LIVE_TRADING=true` and live credentials.
- `ENTRY_TIMEFRAME_MINUTES` must stay `15` for the current strategy implementation.
- `DASHBOARD_AUTH_PASSWORD_HASH` should be generated with the built-in helper, not stored as plaintext.

Generate the dashboard password hash after the image is built:

```bash
docker run --rm alpaca-bot:local alpaca-bot-web-hash-password
```

Copy the printed value into `DASHBOARD_AUTH_PASSWORD_HASH`.

When dashboard auth is enabled, browser visits to `/` and `/metrics` show a sign-in form. Scripted clients can still authenticate with an `Authorization: Basic ...` header.

To rotate the dashboard password later and immediately recreate the `web` container:

```bash
cd /srv/alpaca_bot/current
./scripts/rotate_dashboard_password.sh /etc/alpaca_bot/alpaca-bot.env operator@example.com
```

That command updates the env file, writes the plaintext password to `/etc/alpaca_bot/dashboard_password.txt`, prints the new credentials once, and recreates the local dashboard container.

To update just the Alpaca keys in the existing env file from process environment variables:

```bash
cd /srv/alpaca_bot/current
ALPACA_PAPER_API_KEY=... \
ALPACA_PAPER_SECRET_KEY=... \
./scripts/sync_alpaca_credentials.sh /etc/alpaca_bot/alpaca-bot.env
```

## Automated Nightly Parameter Apply

After `alpaca-bot-nightly` completes successfully, `scripts/apply_candidate.sh` automatically:

1. Reads `/var/lib/alpaca-bot/nightly/candidate.env` (written by the nightly run if a walk-forward
   held candidate was found).
2. Compares the 3 candidate parameters (`BREAKOUT_LOOKBACK_BARS`, `RELATIVE_VOLUME_THRESHOLD`,
   `DAILY_SMA_PERIOD`) against the current values in the system env file.
3. If any param changed, updates the env file and restarts the supervisor via `deploy.sh`.
4. If params are already current (or no `candidate.env` exists), exits cleanly with no restart.

The cron chains both commands with `&&`, so apply only fires when nightly exits 0. Both commands
log to `/var/log/alpaca-bot-nightly.log`.

To verify an apply happened:
```bash
grep "apply_candidate" /var/log/alpaca-bot-nightly.log | tail -5
```

To apply manually (e.g., after a `--dry-run` evolve):
```bash
cd /workspace/alpaca_bot
./scripts/apply_candidate.sh /etc/alpaca_bot/alpaca-bot.env
```

To skip auto-apply for one night (e.g., while investigating an issue), temporarily rename
`candidate.env` before the apply window:
```bash
mv /var/lib/alpaca-bot/nightly/candidate.env /var/lib/alpaca-bot/nightly/candidate.env.hold
```

## First-time setup

1. Check out the repo on the server.
2. Generate the initial env file:

```bash
cd /srv/alpaca_bot/current
./scripts/init_server.sh /etc/alpaca_bot/alpaca-bot.env paper operator@example.com
```

3. Edit `/etc/alpaca_bot/alpaca-bot.env` and replace the placeholder Alpaca keys plus the dashboard auth hash.
4. Build the runtime image:

```bash
cd /srv/alpaca_bot/current
docker build -t alpaca-bot:local .
```

5. Run the deploy helper, which builds the compose services, starts Postgres, applies migrations, starts the local read-only dashboard, and starts the supervisor if valid Alpaca credentials are present:

```bash
cd /srv/alpaca_bot/current
./scripts/deploy.sh /etc/alpaca_bot/alpaca-bot.env
```

The deploy helper now runs a post-deploy ops check automatically:

- it always requires the local dashboard `/healthz` to report `status=ok` and `db=ok`
- it requires `worker_status=fresh` only when the supervisor is expected to be running
- if Alpaca credentials are still placeholders, deploy still succeeds for `postgres + web`, but the worker is explicitly allowed to remain missing

## GitHub Actions deploy

This repo now includes a manual workflow at `.github/workflows/deploy.yml`. It does not read secrets back from GitHub; instead, GitHub injects them into the job at deploy time and the workflow pushes them onto the server by running the checked-in credential-sync script before `./scripts/deploy.sh`.

Required GitHub repository secrets:

- `ALPACA_PAPER_API_KEY`
- `ALPACA_PAPER_SECRET_KEY`
- `DEPLOY_HOST`
- `DEPLOY_USER`
- `DEPLOY_SSH_KEY`

Optional GitHub repository secrets:

- `ALPACA_LIVE_API_KEY`
- `ALPACA_LIVE_SECRET_KEY`
- `DEPLOY_PORT`
- `DEPLOY_HOST_FINGERPRINT`

The workflow is `workflow_dispatch` only. That is intentional; deploys are manual until the trading runtime is fully proven and the market-hours safety policy is tighter.

The remote deploy step also uses `flock /tmp/alpaca-bot-deploy.lock` so two workflow runs cannot modify the server env file and Docker stack at the same time.

## Deploy/update procedure

Run this order on each deploy:

1. Update the checkout to the target revision.
2. Keep `/etc/alpaca_bot/alpaca-bot.env` unchanged unless you are intentionally changing runtime config.
3. Run the deploy helper.

Example:

```bash
cd /srv/alpaca_bot/current
git fetch --all --tags
git checkout <target-revision>
./scripts/deploy.sh /etc/alpaca_bot/alpaca-bot.env
```

If the worker does not start, inspect logs with Docker:

```bash
docker compose -f deploy/compose.yaml logs --tail=200 supervisor
```

To verify the local dashboard:

```bash
curl http://127.0.0.1:18080/healthz
```

To run the same health gate manually from the Docker network:

```bash
cd /srv/alpaca_bot/current
./scripts/ops_check.sh --url http://web:8080/healthz --expect-worker
```

If the supervisor is intentionally absent because Alpaca credentials are not configured yet:

```bash
cd /srv/alpaca_bot/current
./scripts/ops_check.sh --url http://web:8080/healthz --no-expect-worker
```

The health payload now includes worker freshness fields such as `worker_status`, `worker_last_event_type`, and `worker_last_event_at`, so you can distinguish “web is up” from “supervisor is actually alive.” The HTML overview page is available only on the server itself at `http://127.0.0.1:18080/`. Put Caddy in front of it later if you want remote access.

This repo includes an example reverse-proxy config at [deploy/Caddyfile.example](/workspace/alpaca_bot/deploy/Caddyfile.example), but this host may already have another service bound to `:80/:443`. Do not take over the public edge without checking that first.

If you only want to verify the local database is healthy:

```bash
docker compose -f deploy/compose.yaml ps postgres
docker compose -f deploy/compose.yaml logs --tail=100 postgres
```

## Admin command examples

Check current status:

```bash
cd /srv/alpaca_bot/current
./scripts/admin.sh status
```

Halt trading immediately:

```bash
cd /srv/alpaca_bot/current
./scripts/admin.sh halt --reason "manual operator halt"
```

Allow exits only:

```bash
cd /srv/alpaca_bot/current
./scripts/admin.sh close-only --reason "broker investigation"
```

Resume normal trading:

```bash
cd /srv/alpaca_bot/current
./scripts/admin.sh resume --reason "issue cleared"
```

Override mode or strategy version explicitly if needed:

```bash
cd /srv/alpaca_bot/current
./scripts/admin.sh status --mode paper --strategy-version v1-breakout
```

Rotate the dashboard password:

```bash
cd /srv/alpaca_bot/current
./scripts/rotate_dashboard_password.sh /etc/alpaca_bot/alpaca-bot.env operator@example.com
```
