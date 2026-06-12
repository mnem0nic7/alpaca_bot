# Contrarian Strategy Evaluation — 2026-06-11

An adversarial audit of all 11 trading strategies and — more importantly — of the
evaluation machinery itself. The question asked was not "which strategies make
money?" but "are our evaluation methods fooling us?" The answer: they were, in at
least eight distinct ways. After correcting for them, **no strategy in the portfolio
has demonstrated a statistically defensible edge**, and the account's −$28,468
drawdown is ~95% attributable to option strategies whose losses were invisible in
the per-strategy equity P&L view everyone was looking at.

Evidence window: 2026-04-27 → 2026-06-12 (paper trading, strategy_version
`v1-breakout`). Account equity at audit time: $71,531.96 from a $100,000 start.

---

## 1. Methodology weaknesses found

Eight ways the evaluation pipeline was structurally biased toward flattering its
own strategies — or, in the case of items 7 and 8, silently unable to evaluate
them at all. Items 1 and 4 were fixed in this audit (commits
`38191a7`…`1b6bc40`); the rest are documented here with recommendations in §5.

### 1.1 Frictionless fills in every backtest (fixed)

The replay engine filled every order at the ideal price: entries at the limit,
stops exactly at the stop price, exits at the bar close. Zero spread, zero
slippage, zero commission-equivalent. Every historical sweep, backtest, and
nightly tuning score was computed in this frictionless world.

**Fix shipped:** `Settings.replay_slippage_bps` (env `REPLAY_SLIPPAGE_BPS`, now set
to 5.0 in production) applies per-side adverse slippage at all four fill sites in
`src/alpaca_bot/replay/runner.py` — entry fills (line 217), stop exits (line 269),
target exits (line 309), and end-of-scenario closes (line 335) via `_slipped()`
(line 66). Buys fill higher, sells fill lower. The audit in §2 runs every strategy
at 0 bps and 5 bps and reports the cost drag explicitly.

### 1.2 Survivorship / selection bias in the scenario store

`src/alpaca_bot/backfill/cli.py:43` builds the backfill universe from the *current*
static watchlist (`settings.symbols`). The 999 scenario files in
`/var/lib/alpaca-bot/nightly/scenarios/` are therefore the trailing 252 days of
symbols selected *because they are liquid, active large-caps today*. Any strategy
tuned on this store inherits a "these stocks survived and stayed liquid" prior
that live trading does not get to assume.

### 1.3 The OOS gate is too weak to reject overfitting

The nightly walk-forward gate (`src/alpaca_bot/nightly/cli.py`) accepts a candidate
parameter set if it has **as few as 3 out-of-sample trades** (line 191), an
OOS/IS score ratio ≥ 0.6 (line 201, default `--oos-gate-ratio 0.6`), and an
absolute OOS score ≥ 0.2 (line 202, default `--min-oos-score 0.2`). Three trades
cannot distinguish skill from a coin flip; a 0.6 ratio tolerates 40% degradation
out of sample, which is exactly the signature of an overfit candidate. The gate's
one redeeming result: under honest P&L it accepted nothing (§4).

### 1.4 No significance testing anywhere (fixed)

Before this audit, no part of the pipeline asked whether a strategy's mean trade
P&L was distinguishable from zero. A strategy with 8 trades and a lucky $400
winner looked identical to a genuine edge.

**Fix shipped:** `src/alpaca_bot/replay/stats.py` provides
`bootstrap_mean_ci()` (95% CI on mean trade P&L, 10,000 resamples) and
`bootstrap_p_positive()`, refusing to emit an interval below `MIN_SAMPLES = 5`
trades. The audit verdicts in §2 are driven by the CI, not the point estimate:
`negative-edge` (CI entirely below 0), `positive-edge` (CI entirely above 0),
`no-evidence` (CI straddles 0), `insufficient-data` (< 5 trades).

### 1.5 Single-regime evaluation window

Everything — sweeps, backtests, the nightly pipeline — runs on one contiguous
252-trading-day window (`--days` default 252 in both `backfill/cli.py` and
`nightly/cli.py`). The window is whatever regime the last year happened to be.
There is no bull/bear/chop segmentation, no walk-forward across disjoint periods,
no stress window. A strategy that is purely a regime bet (e.g. short-premium bear
strategies in a rising market) cannot be distinguished from a broken one.

### 1.6 Attribution integrity: the books disagree with themselves

Three separate attribution defects, all of which made the per-strategy view
untrustworthy:

- **Option closes recorded in the wrong table.** Option *sells* (premium
  collection) are recorded in `option_orders`, but 84 short-option buy-to-close
  fills totaling **$58,033** were recorded in the equity `orders` table — plus
  $609 of OCC-symbol sells mislabeled with `strategy_name='breakout'`. Result:
  `OptionOrderRepository.list_trade_pnl_by_strategy` (the "official" option P&L)
  reports −$16,192, while full reconciliation puts option losses at ≈ −$26,938.
  The official view undercounts option losses by ~40%.
- **Nightly AuditEvents are back-stamped.** `src/alpaca_bot/nightly/cli.py:249`
  stamps `nightly_sweep_completed` with `created_at = now` captured at run
  *start*. The R7 sweep that finished 2026-06-12 14:24 UTC is stamped
  2026-06-11 14:36 — a 23.8-hour lie that nearly caused this audit to attribute
  the result to a different run.
- **Concurrent nightly runs are possible.** The 22:30 UTC cron nightly fired
  while the manual R7 sweep was still running; both full sweeps ran side by side
  for ~16 hours, sharing CPU and rewriting the same scenario directory
  (`--output-dir /data/scenarios`). The supervisor holds a Postgres advisory
  lock; the nightly pipeline holds nothing.

### 1.7 The replay harness silently evaluates only 8 of 999 scenarios

Discovered while diagnosing the audit results in §2. `evaluate_cycle()` iterates
the *configured* symbol watchlist (`core/engine.py:686`,
`for symbol in (symbols or settings.symbols)`), and `ReplayRunner` never passes
the scenario's own symbol via the engine's `symbols` override parameter
(`core/engine.py:109`). A scenario for any symbol outside the 8-name production
watchlist (AAPL, MSFT, AMZN, NVDA, META, SPY, QQQ, IWM) runs the full per-bar
loop, finds no bars for any watched symbol, and produces zero decisions — no
signals, no rejections, no error. **991 of the 999 scenarios in
`/var/lib/alpaca-bot/nightly/scenarios/` are dead air.** Every "999-scenario"
sweep, backtest, and audit ever run against that store — including the R7 sweep
in §4 and the headline audit table in §2 — was in fact an 8-scenario evaluation
wearing a 999-scenario label.

Verified empirically: replaying a non-watchlist scenario (`AAMI_252d.json`)
through `evaluate_cycle` per-bar produces **zero decision records of any kind**,
while watchlist scenarios produce thousands.

### 1.8 Look-ahead, scenario-constant daily trend gate in replay

`ReplayRunner` passes the scenario's **full** daily bar series to the engine on
every intraday bar (`replay/runner.py:104` —
`{bar.symbol: scenario.daily_bars}`), with no point-in-time slicing. Strategy
trend gates that read the *end* of the series —
`daily_trend_filter_passes` (`strategy/breakout.py:33`,
`daily_bars[-sma_period - 1 : -1]`) and its downtrend twin — therefore evaluate
the **final day of the scenario** on every bar. Two consequences:

- **Look-ahead bias:** when the gate passes, the strategy is trading on the
  knowledge that the symbol ends the year above its 20-day SMA.
- **Scenario-constant gating:** the gate is one boolean per scenario. If the
  symbol's last close is below its SMA20, the strategy emits zero signals for
  the entire 252-day replay — regardless of how many genuine in-trend breakouts
  occurred along the way.

On 2026-06-11 (the nightly store's end date), **all 8 watchlist symbols closed
below their 20-day SMA** — a routine market dip. Combined with §1.7, this made
every trend-gated strategy structurally unable to trade in the audit and in the
R7 sweep. Direct verification: `evaluate_breakout_signal` with point-in-time
daily slices emits 899 valid signals over 30 scenarios; the same scenarios
through the replay harness emit zero.

Of the 11 long strategies, **only momentum is immune**: it reconstructs
point-in-time daily history before applying the trend filter
(`strategy/momentum.py:29-32`, `prior_daily = [b for b in daily_bars if
b.timestamp.date() < today]`). Every other evaluator passes the raw series.
This is why momentum is the only strategy with trades in the §2 audit table —
not because the others have no signals, but because the harness cannot see
them. (Older repo scenarios ending 2026-04-24, when the watchlist was above its
SMA20, produce 13–46 breakout trades per symbol through the identical code —
confirming the gate date, not the strategy, decides the outcome.)

---

## 2. Equity strategies: live results vs. cost-aware replay

### Live matched P&L (source of truth)

Per-strategy realized P&L from `OrderStore.list_trade_pnl_by_strategy` — entries
matched to exits on the same session date, carryover/recovery liquidations
excluded (this is the method that corrected the earlier fictional +$137k figure).
Window 2026-04-27 → 2026-06-12; last equity exit was 2026-05-11, after which the
capacity-starvation bug kept the bot trading-dead until it was fixed.

| Strategy | Live P&L | Trades | Win rate | Enabled today |
|---|---:|---:|---:|---|
| momentum | −$374.99 | 43 | 30.2% | yes |
| vwap_cross | −$342.59 | 22 | 9.1% | **no** (2026-06-11) |
| bb_squeeze | −$234.84 | 13 | 7.7% | **no** (2026-06-11) |
| orb | −$201.44 | 46 | 41.3% | yes |
| ema_pullback | −$187.90 | 20 | 30.0% | yes |
| bull_flag | −$116.06 | 24 | 54.2% | yes |
| vwap_reversion | −$65.45 | 1 | 0.0% | **no** (2026-06-11) |
| failed_breakdown | −$21.72 | 4 | 25.0% | **no** (2026-06-11) |
| breakout | **+$95.59** | 53 | 43.4% | yes |
| **Total equity** | **−$1,449.40** | 226 | | |

Enabled/disabled state comes from `strategy_flag_changed` AuditEvents (the
mechanism is per-strategy DB flags, not env vars); the four disables were applied
2026-06-11 14:27 UTC based on the prior integrity session's findings.

The contrarian read: the *best* equity strategy made $95.59 on 53 trades — a mean
of +$1.80 per trade. That is not an edge; that is noise with good manners. And
breakout's +$95.59 is before asking whether 5 bps of slippage per side would have
erased it (it trades the most, so it pays the most friction).

### Cost-aware, significance-aware replay audit

Every strategy run over all 999 scenarios twice — frictionless (0 bps) and with
5 bps per-side adverse slippage — pooling per-trade P&L and classifying the edge
by bootstrap 95% CI (`alpaca-bot-backtest audit`).

| strategy | scenarios | trades | win rate | profit factor | total P&L | mean/trade | ann. Sharpe | 95% CI mean/trade | p(edge>0) | frictionless P&L | cost drag | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| breakout | 999 | 0 | n/a | n/a | 0.00 | n/a | n/a | n/a | n/a | 0.00 | 0.00 | **insufficient-data** |
| momentum | 999 | 313 | 47.3% | 0.64 | -1123.50 | -3.5895 | -3.14 | [-6.2288, -0.9798] | 0.9950 | 445.40 | 1568.90 | **negative-edge** |
| orb | 999 | 0 | n/a | n/a | 0.00 | n/a | n/a | n/a | n/a | 0.00 | 0.00 | **insufficient-data** |
| high_watermark | 999 | 0 | n/a | n/a | 0.00 | n/a | n/a | n/a | n/a | 0.00 | 0.00 | **insufficient-data** |
| ema_pullback | 999 | 0 | n/a | n/a | 0.00 | n/a | n/a | n/a | n/a | 0.00 | 0.00 | **insufficient-data** |
| vwap_reversion | 999 | 0 | n/a | n/a | 0.00 | n/a | n/a | n/a | n/a | 0.00 | 0.00 | **insufficient-data** |
| gap_and_go | 999 | 0 | n/a | n/a | 0.00 | n/a | n/a | n/a | n/a | 0.00 | 0.00 | **insufficient-data** |
| bull_flag | 999 | 0 | n/a | n/a | 0.00 | n/a | n/a | n/a | n/a | 0.00 | 0.00 | **insufficient-data** |
| vwap_cross | 999 | 0 | n/a | n/a | 0.00 | n/a | n/a | n/a | n/a | 0.00 | 0.00 | **insufficient-data** |
| bb_squeeze | 999 | 0 | n/a | n/a | 0.00 | n/a | n/a | n/a | n/a | 0.00 | 0.00 | **insufficient-data** |
| failed_breakdown | 999 | 0 | n/a | n/a | 0.00 | n/a | n/a | n/a | n/a | 0.00 | 0.00 | **insufficient-data** |

**Read this table through §1.7 and §1.8 before drawing any conclusion.** The ten
`insufficient-data` rows are **harness artifacts, not strategy verdicts**: 991
of the 999 scenarios were never evaluated (§1.7), and on the 8 that were, the
scenario-constant trend gate was false for every symbol (§1.8). The zeros say
nothing about whether those strategies have signals — direct evaluation shows
breakout alone has hundreds in the same data.

The momentum row is the only row with evidence, and it is damning where it
counts:

- Momentum is the one strategy whose evaluator handles the daily series
  correctly, so its 313 trades (from the 8 watchlist symbol-years, ~39 per
  symbol) are a fair point-in-time sample.
- **Frictionless, it made +$445. At 5 bps per side, it lost −$1,124.** The
  entire apparent edge — and more — is inside the cost model the pipeline
  ignored until this audit. Cost drag ($1,569) is 3.5× the frictionless profit.
- The bootstrap 95% CI on mean trade P&L is [−$6.23, −$0.98] — entirely below
  zero. This is not "no edge detected"; it is affirmative evidence of a
  **negative** edge after realistic costs (p(edge>0) ≈ 0.5%).
- This corroborates the live result: momentum is also the biggest equity loser
  in production (−$375 over 43 trades).

The contrarian conclusion from the one valid measurement: the only strategy we
can honestly backtest is a strategy that reliably loses money once friction
exists. Every other equity strategy is currently **unevaluatable** until the
§1.7/§1.8 harness defects are fixed and the audit re-run.

---

## 3. Option strategies: where the account actually went

The headline finding of this audit. The per-strategy equity table above accounts
for −$1,449 of a −$28,468 account drawdown. The other ~95% is options:

| Component | Amount |
|---|---:|
| Gross premium collected (`option_orders` sells) | +$30,486 |
| — bear_orb | +$28,406 |
| — bear_breakdown | +$1,300 |
| — bear_ema_rejection | +$780 |
| Short-option buy-to-close fills (recorded in `orders`, 84 fills) | −$58,033 |
| OCC-symbol sells mislabeled `breakout` | +$609 |
| **Net option P&L** | **≈ −$26,938** |
| Equity strategies (matched) | −$1,449.40 |
| **Reconstructed total** | **≈ −$28,387** |
| Actual account drawdown ($100,000 → $71,531.96) | −$28,468 |
| Unexplained residual | ~$81 |

The book is flat (the `positions` table is empty), so these losses are fully
realized, not mark-to-market.

The matched per-strategy option view (`OptionOrderRepository.
list_trade_pnl_by_strategy`, ×100 contract multiplier) — which undercounts per
§1.6 but attributes correctly where it can match:

| Strategy | Matched P&L | Trades | Wins | Win rate |
|---|---:|---:|---:|---:|
| bear_orb | −$14,232 | 396 | 64 | 16.2% |
| bear_breakdown | −$1,220 | 43 | 0 | **0.0%** |
| bear_ema_rejection | −$740 | 39 | 0 | **0.0%** |

Two strategies have **zero wins across 82 matched trades**. bear_orb collected
$28k of premium and gave back substantially more — the classic short-premium
profile: many small wins masked by the equity curve until the buybacks land.

Order-dispatch health makes it worse: bear_orb had **1,880 failed option orders**
against 396 filled sells; bear_breakdown 188 failed; bear_ema_rejection 190
failed. The strategies were not only losing — most of what they tried to do never
reached the market, so the realized result is also not a faithful sample of the
strategy's intent.

These three strategies cannot be replay-audited (§2 covers equity replay only —
the replay engine does not simulate option chains), which means the worst
performers in the account are exactly the ones the evaluation pipeline cannot
see. They were also live the whole time with no per-strategy option loss limit
beyond the rolling `OPTION_STRATEGY_MAX_ROLLING_LOSS_USD=500` / 7-day window —
which the $26.9k aggregate loss demonstrates was insufficient as configured.

---

## 4. Sweep integrity: the nightly pipeline judged honestly

The R7 sweep — the first full run since honest P&L scoring landed — was this
audit's natural experiment: 999 scenarios × 11 strategies, 80/20 IS/OOS
walk-forward, evolutionary optimizer, 23.8 hours of compute.

**Result: 0 of 11 strategies produced an acceptable candidate.** The
`nightly_sweep_completed` AuditEvent payload:

```json
{"best_score": null, "best_strategy": null, "strategy_count": 11,
 "candidates_accepted": 0, "candidate_env_written": false}
```

breakout and momentum were observed live skipping at "no scored candidates"; no
`candidate.env` was written; current parameters remain active. The
`tuning_results` table has zero rows from the run window, so per-strategy sweep
detail beyond the live log is unrecoverable.

At the time, "0/11 accepted" read as the gate doing its job under honest
scoring. **The §1.7/§1.8 diagnosis rewrites that interpretation.** The sweep
uses the same `ReplayRunner` (`tuning/sweep.py:248,305,360`), so it inherited
both harness defects: 991 of its 999 scenarios were never evaluated, and on the
remaining 8 every trend-gated strategy was scenario-constant-blocked because all
8 symbols ended the window below their SMA20. Ten of the eleven strategies
*could not have produced a candidate no matter what parameters the optimizer
tried* — their search space was a flat zero. The honest reading:

- **For 10 strategies, the sweep result is void.** Not "no parameters work" but
  "no parameters were ever actually tested." 23.8 hours of compute spent
  optimizing functions that were identically zero.
- **For momentum, the rejection is probably genuine.** Momentum's evaluator is
  point-in-time-correct, it traded in the sweep data, and the §2 audit
  independently shows its mean trade P&L is negative with 95% confidence at
  5 bps. An optimizer failing to find an acceptable momentum candidate is
  consistent with there being nothing to find.
- **The same defects are regime-coupled flakiness in every past sweep.** Because
  the trend gate is evaluated at the scenario's final date, an entire sweep's
  outcome for 10 strategies flips on whether the watchlist happened to be above
  its 20-day SMA on backfill day. Sweeps run in a dip find nothing; sweeps run
  in a rally "work". Historical sweep-to-sweep comparisons are meaningless
  until this is fixed.
- The operational defects stand regardless: no per-strategy scores persisted,
  the completion event is timestamped 23.8 hours before completion (§1.6), and
  a second cron-triggered sweep overlapped it for 16 hours.

---

## 5. Recommendations

In priority order:

1. **Fix the replay harness before trusting any backtest again.** Two small,
   surgical changes: (a) `ReplayRunner` must pass `symbols=(scenario.symbol,)`
   to `evaluate_cycle()` — the engine already accepts the override
   (`core/engine.py:109`); (b) the runner must slice daily bars point-in-time
   per replay date instead of passing the full series
   (`replay/runner.py:104`) — or every evaluator must adopt momentum's
   `prior_daily` reconstruction (`strategy/momentum.py:29-32`). Then re-run
   this audit and the sweep; until then, all replay-derived verdicts for the
   ten non-momentum strategies are void (§1.7, §1.8). The fix also unlocks the
   scenario store's actual breadth: 999 symbol-years instead of 8.
2. **Raise the OOS gate's minimum trade count and gate on the CI lower bound.**
   `min_trades=3` → at least 30, and replace the point-score gate with
   "bootstrap 95% CI lower bound on OOS mean trade P&L > 0" using
   `replay/stats.py` (already shipped). A candidate that can't show 30 OOS trades
   with a positive CI lower bound is indistinguishable from luck.
3. **Treat the option strategies as unevaluated and act accordingly.** bear_orb,
   bear_breakdown, and bear_ema_rejection produced ≈ −$26,938 (95% of the
   drawdown), two of them with zero matched wins, outside the replay engine's
   visibility. Either disable them via the existing strategy flags until option
   replay exists, or cap them with a much tighter rolling loss limit than the
   current $500/7d (which demonstrably did not bound aggregate losses).
4. **Unify option close recording.** Buy-to-close fills belong in
   `option_orders` with correct strategy attribution, not in the equity `orders`
   table (and never as `strategy_name='breakout'`). Until then, every
   per-strategy P&L report silently understates option losses by ~40%.
5. **Rotate the backfill watchlist.** Sample scenario symbols from a
   point-in-time universe (or at minimum include delisted/declined names) instead
   of the current static `settings.symbols` (§1.2), so sweeps stop inheriting
   survivorship bias.
6. **Evaluate across regime windows.** Split the 252-day store into disjoint
   regime segments (or use multiple non-overlapping years) and require a
   candidate to survive each, not just the pooled window.
7. **Fix the small integrity defects while they're cheap.** Stamp
   `nightly_sweep_completed` with the actual completion time
   (`nightly/cli.py:249`); add an advisory lock (or lockfile) so cron and manual
   nightly runs cannot overlap; persist per-strategy sweep scores even when no
   candidate is accepted, so a 24-hour run always leaves evidence.
8. **Keep the cost model on.** `REPLAY_SLIPPAGE_BPS=5.0` is now in the
   production env; all future sweeps and the OOS gate evaluate with friction.
   Revisit the 5 bps figure once live fill-quality data is collected.

---

*Generated by the 2026-06-11 contrarian strategy audit
(spec: `docs/superpowers/specs/2026-06-11-contrarian-strategy-audit-design.md`,
plan: `docs/superpowers/plans/2026-06-11-contrarian-strategy-audit.md`).
Replay audit tooling: commits `38191a7`, `6ca453c`, `089803c`, `7442be5`,
`1b6bc40`. Full audit table: `2026-06-11-audit-table.md`.*
