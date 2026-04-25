# alpaca_bot Operator Deployment

This repo currently deploys as a self-hosted Docker stack on one server: local `postgres`, a long-running worker container `alpaca-bot-supervisor`, and a read-only local dashboard `alpaca-bot-web`. Operational control remains CLI-only through `alpaca-bot-admin`.

## Runtime shape

- `alpaca-bot-supervisor` is the server process to keep running.
- `alpaca-bot-web` is a read-only FastAPI dashboard bound to `127.0.0.1:18080`.
- The dashboard supports optional HTTP Basic auth using a single pre-provisioned operator account from env.
- `postgres` is the local state store for orders, positions, audit events, and status.
- `alpaca-bot-migrate` applies SQL migrations in `migrations/`.
- `alpaca-bot-admin` is for operator actions such as `status`, `halt`, `close-only`, and `resume`.
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
MARKET_DATA_FEED=sip
SYMBOLS=AAPL,MSFT,SPY

DAILY_SMA_PERIOD=20
BREAKOUT_LOOKBACK_BARS=20
RELATIVE_VOLUME_LOOKBACK_BARS=20
RELATIVE_VOLUME_THRESHOLD=1.5
ENTRY_TIMEFRAME_MINUTES=15
RISK_PER_TRADE_PCT=0.0025
MAX_POSITION_PCT=0.05
MAX_OPEN_POSITIONS=3
DAILY_LOSS_LIMIT_PCT=0.01
STOP_LIMIT_BUFFER_PCT=0.001
BREAKOUT_STOP_BUFFER_PCT=0.001
ENTRY_STOP_PRICE_BUFFER=0.01
ENTRY_WINDOW_START=10:00
ENTRY_WINDOW_END=15:30
FLATTEN_TIME=15:45

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

The HTML overview page is available only on the server itself at `http://127.0.0.1:18080/`. Put Caddy in front of it later if you want remote access.

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
