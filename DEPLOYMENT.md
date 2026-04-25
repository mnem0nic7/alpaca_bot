# alpaca_bot Operator Deployment

This repo currently deploys as a single long-running worker container: `alpaca-bot-supervisor`. There is no dashboard or separate web service yet. Operational control is CLI-only through `alpaca-bot-admin`.

## Runtime shape

- `alpaca-bot-supervisor` is the server process to keep running.
- `alpaca-bot-migrate` applies SQL migrations in `migrations/`.
- `alpaca-bot-admin` is for operator actions such as `status`, `halt`, `close-only`, and `resume`.
- `alpaca-bot-trader` exists as a one-shot startup/reconciliation entrypoint, but it is not the primary deployed service for the current runtime.

## Recommended server layout

Use any equivalent layout you prefer, but keep the environment file outside the repo checkout.

- Repo checkout: `/srv/alpaca_bot/current`
- Environment file: `/etc/alpaca_bot/alpaca-bot.env`

The application reads settings from process environment variables. It does not load a repo-local `.env` file on its own, so your deploy tooling must load `/etc/alpaca_bot/alpaca-bot.env` before running commands. The checked-in Docker Compose stack lives at `deploy/compose.yaml`, and the checked-in helper scripts are `scripts/deploy.sh` and `scripts/admin.sh`.

## Environment file

Example `/etc/alpaca_bot/alpaca-bot.env`:

```dotenv
TRADING_MODE=paper
ENABLE_LIVE_TRADING=false
STRATEGY_VERSION=v1-breakout
DATABASE_URL=postgresql://alpaca_bot:secret@127.0.0.1:5432/alpaca_bot
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
```

Notes:

- `TRADING_MODE=paper` requires paper credentials.
- `TRADING_MODE=live` requires `ENABLE_LIVE_TRADING=true` and live credentials.
- `ENTRY_TIMEFRAME_MINUTES` must stay `15` for the current strategy implementation.

## First-time setup

1. Check out the repo on the server.
2. Create `/etc/alpaca_bot/alpaca-bot.env` with the runtime values.
3. Build the runtime image:

```bash
cd /srv/alpaca_bot/current
docker build -t alpaca-bot:local .
```

4. Run the deploy helper, which builds the compose services, applies migrations, and starts the supervisor:

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

If the service fails after restart, inspect logs with Docker:

```bash
docker compose -f deploy/compose.yaml logs --tail=200 supervisor
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
