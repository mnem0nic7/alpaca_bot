# Bull-flag EOD-loss mitigation - 2026-07-06

Purpose: investigate the clean-window paper losses where `bull_flag` entries
that did not hit protective stops were carried to session-end losses.

Live context at triage time:

- Mode: `paper`
- Approved strategy: `bull_flag`
- Clean-window sealed P&L: `-15.73`
- Completed trades: 4
- EOD loss symbols: `DDOG`, `PANW`
- Proof status: `pending`, because the clean window still lacks enough
  favorable evidence.

## Replay attribution hardening

Before this check, replay summaries collapsed all non-stop exits to EOD-like
labels in some paths. That made it hard to tell whether an early strategy exit
would actually reduce EOD loss exposure.

Tooling fix: replay exits now preserve explicit strategy exit reasons such as
`viability_vwap_breakdown` while still normalizing true session-end flattening
to `eod`. The paper proof output also reports EOD-loss symbols and sealed
EOD-loss share, so this failure mode is visible directly in the proof readout.

## Coarse exit-filter sweep

Command:

```bash
timeout 900s bash -lc 'set -a; source /etc/alpaca_bot/alpaca-bot.env; set +a; \
  export ENABLE_LIVE_TRADING=false TRADING_MODE=paper PYTHONPATH=src; \
  python3 -m alpaca_bot.replay.cli lever-sweep \
    --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
    --strategy bull_flag \
    --sample-size 40 \
    --sample-seed bull-flag-exit-filter-20260706 \
    --slippage-bps 2 \
    --coarse \
    --top-k 5 \
    --output /tmp/bull_flag_exit_filter_lever_40.md'
```

Result:

| rank | lever | IS ci_low | IS mean | IS trades | IS verdict | OOS ci_low | OOS verdict |
|---:|---|---:|---:|---:|---|---:|---|
| 1 | `L_vwap_breakdown_exit:on` | -1.7012 | 0.3269 | 48 | `no-evidence` | -3.9041 | `no-evidence` |
| 2 | `A_initial_stop:atr_stop_multiplier=1.5` | -3.0895 | -0.5331 | 48 | `no-evidence` | -4.4587 | `no-evidence` |
| 3 | `baseline` | -3.2018 | -0.5350 | 48 | `no-evidence` | -3.8219 | `no-evidence` |
| 4 | `B_trail_atr:trailing_stop_atr_multiplier=2.5` | -3.2018 | -0.5350 | 48 | `no-evidence` | -3.8219 | `no-evidence` |
| 5 | `C_trail_trigger:trailing_stop_profit_trigger_r=1.5` | -3.2018 | -0.5350 | 48 | `no-evidence` | -3.8219 | `no-evidence` |

The VWAP-breakdown exit improved the in-sample lower bound but failed
walk-forward validation. Trend exit, profit target, VWAP-off, and the generic
relative-volume lever did not improve the in-sample result on this sample.

Conclusion: there is no approved exit-filter change from this sweep. The
current live mitigation remains proof-gating plus the stricter entry-quality
guard; do not enable VWAP-breakdown early exits in paper without fresh OOS
evidence.

## Loss-control sweep

Because the DDOG/PANW losses sat inside the current 5% stop envelope, a
bounded sweep tested tighter `MAX_STOP_PCT` and dollar loss caps before any
paper setting change.

Command:

```bash
timeout 900s bash -lc 'set -a; source /etc/alpaca_bot/alpaca-bot.env; set +a; \
  export ENABLE_LIVE_TRADING=false TRADING_MODE=paper PYTHONPATH=src; \
  python3 - <<PY
from alpaca_bot.config import Settings
from alpaca_bot.replay.runner import ReplayRunner
from alpaca_bot.replay.lever_sweep import LeverPoint, run_lever_sweep
from alpaca_bot.replay.cli import _select_scenario_paths
import argparse

args = argparse.Namespace(
    scenario_dir="/var/lib/alpaca-bot/nightly/scenarios",
    limit=0,
    sample_size=60,
    sample_seed="bull-flag-loss-control-20260706",
)
paths = _select_scenario_paths(args)
scenarios = [ReplayRunner.load_scenario(p) for p in paths]
settings = Settings.from_env()
grid = [
    LeverPoint("baseline", {}),
    LeverPoint("N_max_stop_pct:0.04", {"max_stop_pct": 0.04}),
    LeverPoint("N_max_stop_pct:0.03", {"max_stop_pct": 0.03}),
    LeverPoint("N_max_stop_pct:0.02", {"max_stop_pct": 0.02}),
    LeverPoint("O_max_loss_per_trade:15", {"max_loss_per_trade_dollars": 15.0}),
    LeverPoint("O_max_loss_per_trade:10", {"max_loss_per_trade_dollars": 10.0}),
    LeverPoint("O_max_loss_per_trade:5", {"max_loss_per_trade_dollars": 5.0}),
    LeverPoint("P_stop3_loss10", {"max_stop_pct": 0.03, "max_loss_per_trade_dollars": 10.0}),
    LeverPoint("Q_stop2_loss10", {"max_stop_pct": 0.02, "max_loss_per_trade_dollars": 10.0}),
]
run_lever_sweep(
    scenarios=scenarios,
    base_settings=settings,
    strategy="bull_flag",
    grid=grid,
    slippage_bps=2.0,
    walk_forward=True,
    top_k=5,
)
PY'
```

Result:

| rank | lever | IS ci_low | IS mean | IS trades | IS verdict | OOS ci_low | OOS verdict |
|---:|---|---:|---:|---:|---|---:|---|
| 1 | `baseline` | 0.6638 | 2.5870 | 69 | `positive-edge` | -2.2587 | `no-evidence` |
| 2 | `O_max_loss_per_trade:15` | 0.4574 | 1.9579 | 67 | `positive-edge` | -1.7269 | `no-evidence` |
| 3 | `O_max_loss_per_trade:10` | 0.2720 | 1.2595 | 63 | `positive-edge` | -1.4601 | `no-evidence` |
| 4 | `O_max_loss_per_trade:5` | 0.1749 | 0.7321 | 52 | `positive-edge` | -1.0029 | `no-evidence` |
| 5 | `N_max_stop_pct:0.04` | 0.1126 | 2.3900 | 69 | `positive-edge` | -2.9903 | `no-evidence` |

Tighter loss caps reduced OOS downside in this sample, but every tested point
weakened the in-sample lower bound versus baseline and none held a non-negative
OOS CI lower bound.

Conclusion: do not tighten `MAX_STOP_PCT` or `MAX_LOSS_PER_TRADE_DOLLARS` from
this evidence. The loss-control path needs a better thesis than generic stop
tightening.

## Entry-execution buffer sweep

The paper proof still reports a raw fill-rate warning, so a small diagnostic
sweep tested whether changing the entry stop buffer or stop-limit buffer could
improve execution quality without weakening edge.

Command:

```bash
timeout 900s bash -lc 'set -a; source /etc/alpaca_bot/alpaca-bot.env; set +a; \
  export ENABLE_LIVE_TRADING=false TRADING_MODE=paper PYTHONPATH=src; \
  python3 - <<PY
from alpaca_bot.config import Settings
from alpaca_bot.replay.runner import ReplayRunner
from alpaca_bot.replay.lever_sweep import LeverPoint, run_lever_sweep
from alpaca_bot.replay.cli import _select_scenario_paths
import argparse

args = argparse.Namespace(
    scenario_dir="/var/lib/alpaca-bot/nightly/scenarios",
    limit=0,
    sample_size=60,
    sample_seed="bull-flag-entry-exec-20260706",
)
paths = _select_scenario_paths(args)
scenarios = [ReplayRunner.load_scenario(p) for p in paths]
settings = Settings.from_env()
grid = [
    LeverPoint("baseline", {}),
    LeverPoint("R_stop_limit_buffer:0.00025", {"stop_limit_buffer_pct": 0.00025}),
    LeverPoint("R_stop_limit_buffer:0.001", {"stop_limit_buffer_pct": 0.001}),
    LeverPoint("R_stop_limit_buffer:0.002", {"stop_limit_buffer_pct": 0.002}),
    LeverPoint("S_entry_stop_buffer:0.01", {"entry_stop_price_buffer": 0.01}),
    LeverPoint("S_entry_stop_buffer:0.03", {"entry_stop_price_buffer": 0.03}),
    LeverPoint("S_entry_stop_buffer:0.05", {"entry_stop_price_buffer": 0.05}),
    LeverPoint(
        "T_entry_lower_limit_wider",
        {"entry_stop_price_buffer": 0.01, "stop_limit_buffer_pct": 0.001},
    ),
    LeverPoint(
        "U_entry_higher_limit_wider",
        {"entry_stop_price_buffer": 0.03, "stop_limit_buffer_pct": 0.001},
    ),
]
run_lever_sweep(
    scenarios=scenarios,
    base_settings=settings,
    strategy="bull_flag",
    grid=grid,
    slippage_bps=2.0,
    walk_forward=True,
    top_k=5,
)
PY'
```

Result:

| rank | lever | IS ci_low | IS mean | IS trades | IS verdict | OOS ci_low | OOS verdict |
|---:|---|---:|---:|---:|---|---:|---|
| 1 | `R_stop_limit_buffer:0.00025` | 0.9350 | 3.3473 | 49 | `positive-edge` | -2.6207 | `no-evidence` |
| 2 | `S_entry_stop_buffer:0.01` | 0.7507 | 3.2717 | 50 | `positive-edge` | -2.1462 | `no-evidence` |
| 3 | `baseline` | 0.7487 | 3.1247 | 50 | `positive-edge` | -2.6335 | `no-evidence` |
| 4 | `T_entry_lower_limit_wider` | 0.5543 | 2.8861 | 54 | `positive-edge` | -2.5233 | `no-evidence` |
| 5 | `R_stop_limit_buffer:0.001` | 0.4231 | 2.7563 | 54 | `positive-edge` | -3.4707 | `no-evidence` |

The tighter stop-limit buffer improved the in-sample lower bound, and the
current entry stop buffer had the least-bad OOS lower bound. No tested point
held a non-negative OOS CI lower bound.

Conclusion: do not change `STOP_LIMIT_BUFFER_PCT` or
`ENTRY_STOP_PRICE_BUFFER` from this evidence. These levers were added to the
standard replay sweep grid so future diagnostics cover execution-buffer
changes without a one-off script.

## VWAP-entry diagnostic correction

While revisiting the clean-window DDOG/PANW EOD losses, the coarse replay grid
showed a diagnostic bug: `G_vwap` was hardcoded to `off`. Because live paper
already has `ENABLE_VWAP_ENTRY_FILTER=false`, the coarse grid was not actually
testing the missing VWAP-on hypothesis. The grid now toggles VWAP relative to
the baseline, matching the OFAT grid.

Command:

```bash
timeout 900s bash -lc 'set -a; source /etc/alpaca_bot/alpaca-bot.env; set +a; \
  export ENABLE_LIVE_TRADING=false TRADING_MODE=paper PYTHONPATH=src; \
  python3 - <<PY
from alpaca_bot.config import Settings
from alpaca_bot.replay.runner import ReplayRunner
from alpaca_bot.replay.lever_sweep import LeverPoint, run_lever_sweep
from alpaca_bot.replay.cli import _select_scenario_paths
import argparse

args = argparse.Namespace(
    scenario_dir="/var/lib/alpaca-bot/nightly/scenarios",
    limit=0,
    sample_size=80,
    sample_seed="bull-flag-vwap-entry-20260706",
)
paths = _select_scenario_paths(args)
scenarios = [ReplayRunner.load_scenario(p) for p in paths]
settings = Settings.from_env()
grid = [
    LeverPoint("baseline", {}),
    LeverPoint("G_vwap:on", {"enable_vwap_entry_filter": True}),
    LeverPoint("L_vwap_breakdown_exit:on", {"enable_vwap_breakdown_exit": True}),
    LeverPoint(
        "M_vwap_breakdown_exit:on,min_bars=2",
        {"enable_vwap_breakdown_exit": True, "vwap_breakdown_min_bars": 2},
    ),
    LeverPoint(
        "V_vwap_entry_and_breakdown",
        {"enable_vwap_entry_filter": True, "enable_vwap_breakdown_exit": True},
    ),
    LeverPoint(
        "W_vwap_entry_and_breakdown_min2",
        {
            "enable_vwap_entry_filter": True,
            "enable_vwap_breakdown_exit": True,
            "vwap_breakdown_min_bars": 2,
        },
    ),
]
run_lever_sweep(
    scenarios=scenarios,
    base_settings=settings,
    strategy="bull_flag",
    grid=grid,
    slippage_bps=2.0,
    walk_forward=True,
    top_k=6,
)
PY'
```

Result:

| rank | lever | IS ci_low | IS mean | IS trades | IS verdict | OOS ci_low | OOS verdict |
|---:|---|---:|---:|---:|---|---:|---|
| 1 | `L_vwap_breakdown_exit:on` | -0.0965 | 1.2520 | 67 | `no-evidence` | -1.9589 | `no-evidence` |
| 2 | `M_vwap_breakdown_exit:on,min_bars=2` | -0.0965 | 1.2520 | 67 | `no-evidence` | -1.9589 | `no-evidence` |
| 3 | `baseline` | -0.1205 | 1.4268 | 67 | `no-evidence` | -1.2321 | `no-evidence` |
| 4 | `V_vwap_entry_and_breakdown` | -0.1800 | 1.1717 | 67 | `no-evidence` | -1.9589 | `no-evidence` |
| 5 | `W_vwap_entry_and_breakdown_min2` | -0.1800 | 1.1717 | 67 | `no-evidence` | -1.9589 | `no-evidence` |
| 6 | `G_vwap:on` | -0.1942 | 1.3620 | 67 | `no-evidence` | -1.2321 | `no-evidence` |

VWAP-on did not reduce trade count on this sample and weakened the in-sample
CI lower bound. VWAP-breakdown exits slightly improved in-sample lower bound,
but worsened OOS lower bound versus baseline.

Conclusion: do not enable `ENABLE_VWAP_ENTRY_FILTER` or
`ENABLE_VWAP_BREAKDOWN_EXIT` from this evidence. The corrected coarse-grid
toggle should remain so future sweeps can test the live opposite setting.

## Earlier-flatten sweep

DDOG and PANW were not late entries; both entered around 10:30 ET and were held
until the normal 15:45 ET flatten. A direct EOD-loss mitigation hypothesis is
to flatten earlier, but this must be tested with `ENTRY_WINDOW_END` moved
earlier at the same time so settings remain valid.

Command:

```bash
timeout 900s bash -lc 'set -a; source /etc/alpaca_bot/alpaca-bot.env; set +a; \
  export ENABLE_LIVE_TRADING=false TRADING_MODE=paper PYTHONPATH=src; \
  python3 - <<PY
from datetime import time
from alpaca_bot.config import Settings
from alpaca_bot.replay.runner import ReplayRunner
from alpaca_bot.replay.lever_sweep import LeverPoint, run_lever_sweep
from alpaca_bot.replay.cli import _select_scenario_paths
import argparse

args = argparse.Namespace(
    scenario_dir="/var/lib/alpaca-bot/nightly/scenarios",
    limit=0,
    sample_size=80,
    sample_seed="bull-flag-earlier-flatten-20260706",
)
paths = _select_scenario_paths(args)
scenarios = [ReplayRunner.load_scenario(p) for p in paths]
settings = Settings.from_env()
grid = [
    LeverPoint("baseline", {}),
    LeverPoint(
        "X_flatten_1530_entry_1515",
        {"flatten_time": time(15, 30), "entry_window_end": time(15, 15)},
    ),
    LeverPoint(
        "Y_flatten_1515_entry_1500",
        {"flatten_time": time(15, 15), "entry_window_end": time(15, 0)},
    ),
    LeverPoint(
        "Z_flatten_1500_entry_1445",
        {"flatten_time": time(15, 0), "entry_window_end": time(14, 45)},
    ),
    LeverPoint(
        "AA_flatten_1445_entry_1430",
        {"flatten_time": time(14, 45), "entry_window_end": time(14, 30)},
    ),
]
run_lever_sweep(
    scenarios=scenarios,
    base_settings=settings,
    strategy="bull_flag",
    grid=grid,
    slippage_bps=2.0,
    walk_forward=True,
    top_k=5,
)
PY'
```

Result:

| rank | lever | IS ci_low | IS mean | IS trades | IS verdict | OOS ci_low | OOS verdict |
|---:|---|---:|---:|---:|---|---:|---|
| 1 | `Y_flatten_1515_entry_1500` | -0.6855 | 0.9205 | 84 | `no-evidence` | -1.6971 | `no-evidence` |
| 2 | `X_flatten_1530_entry_1515` | -0.7068 | 0.8410 | 87 | `no-evidence` | -1.6451 | `no-evidence` |
| 3 | `Z_flatten_1500_entry_1445` | -0.8068 | 0.8019 | 82 | `no-evidence` | -1.4201 | `no-evidence` |
| 4 | `baseline` | -0.8082 | 0.7633 | 87 | `no-evidence` | -1.8974 | `no-evidence` |
| 5 | `AA_flatten_1445_entry_1430` | -0.9910 | 0.5918 | 81 | `no-evidence` | -1.5707 | `no-evidence` |

Earlier flattening produced small in-sample improvements for 15:30 and 15:15
but every tested point still had a negative in-sample and OOS CI lower bound.

Conclusion: do not move `FLATTEN_TIME` or `ENTRY_WINDOW_END` from this
evidence. Paired earlier-flatten points were added to the standard replay grid
so EOD-loss diagnostics remain valid and repeatable.

## No-follow-through exit sweep

Hypothesis: EOD losses may be reduced by cutting long equity trades that have
been open long enough, never reached a small favorable excursion, and are back
at or below entry. The implementation is guarded by
`ENABLE_NO_FOLLOW_THROUGH_EXIT=false` by default and emits
`no_follow_through` when enabled.

Command:

```bash
timeout 900s bash -lc 'set -a; source /etc/alpaca_bot/alpaca-bot.env; set +a; \
  export ENABLE_LIVE_TRADING=false TRADING_MODE=paper PYTHONPATH=src; \
  python3 - <<PY
from argparse import Namespace
from pathlib import Path
from alpaca_bot.config import Settings
from alpaca_bot.replay.cli import _select_scenario_paths
from alpaca_bot.replay.lever_sweep import LeverPoint, format_lever_sweep_markdown, run_lever_sweep
from alpaca_bot.replay.runner import ReplayRunner

args = Namespace(
    scenario_dir="/var/lib/alpaca-bot/nightly/scenarios",
    limit=0,
    sample_size=80,
    sample_seed="bull-flag-no-follow-through-20260706",
)
paths = _select_scenario_paths(args)
scenarios = [ReplayRunner.load_scenario(path) for path in paths]
settings = Settings.from_env()
points = [LeverPoint("baseline", {})]
for minutes in (45, 60, 90, 120):
    for pct in (0.001, 0.0025, 0.005):
        points.append(
            LeverPoint(
                f"Q_no_follow_through:{minutes}m@{pct:g}",
                {
                    "enable_no_follow_through_exit": True,
                    "no_follow_through_exit_minutes": minutes,
                    "no_follow_through_min_favorable_pct": pct,
                },
            )
        )
rows = run_lever_sweep(
    scenarios=scenarios,
    base_settings=settings,
    strategy="bull_flag",
    grid=points,
    slippage_bps=2.0,
    walk_forward=True,
    top_k=8,
)
Path("/tmp/bull_flag_no_follow_through_80.md").write_text(
    format_lever_sweep_markdown(rows, strategy="bull_flag", slippage_bps=2.0)
)
PY'
```

Result:

| rank | lever | IS ci_low | IS mean | IS trades | IS verdict | OOS ci_low | OOS verdict |
|---:|---|---:|---:|---:|---|---:|---|
| 1 | `Q_no_follow_through:90m@0.0025` | 0.3167 | 1.6321 | 85 | `positive-edge` | -2.0458 | `no-evidence` |
| 2 | `Q_no_follow_through:60m@0.0025` | 0.0370 | 1.3345 | 85 | `positive-edge` | -1.3827 | `no-evidence` |
| 3 | `Q_no_follow_through:120m@0.0025` | 0.0359 | 1.4249 | 85 | `positive-edge` | -2.2812 | `no-evidence` |
| 4 | `Q_no_follow_through:90m@0.001` | 0.0289 | 1.4577 | 85 | `positive-edge` | -1.4840 | `no-evidence` |
| 5 | `Q_no_follow_through:120m@0.001` | -0.0459 | 1.4192 | 85 | `no-evidence` | -1.7267 | `no-evidence` |
| 6 | `baseline` | -0.0754 | 1.3672 | 85 | `no-evidence` | -1.8367 | `no-evidence` |
| 7 | `Q_no_follow_through:60m@0.001` | -0.1307 | 1.3116 | 85 | `no-evidence` | -1.4969 | `no-evidence` |
| 8 | `Q_no_follow_through:45m@0.001` | -0.1849 | 1.2677 | 85 | `no-evidence` | -1.4420 | `no-evidence` |

Several variants improved the in-sample lower bound, especially
`90m@0.0025`, but every OOS row remained negative. This is not stable enough
to promote.

Conclusion: keep `ENABLE_NO_FOLLOW_THROUGH_EXIT=false`. The rule remains
available as a disabled diagnostic lever and was added to the standard
`Q_no_follow_through` replay grid for repeatable retesting.

## Later entry-window start sweep

Hypothesis: the clean-window EOD losses were concentrated in the first tradable
window, so starting bull-flag entries later might avoid weak morning breakouts.

An 80-scenario probe showed a tempting OOS-only signal:

| lever | IS ci_low | IS trades | IS verdict | OOS ci_low | OOS verdict |
|---|---:|---:|---|---:|---|
| `AB_start:11:30` | -0.0707 | 33 | `no-evidence` | 1.1267 | `positive-edge` |
| `baseline` | -0.2173 | 73 | `no-evidence` | -0.2786 | `no-evidence` |
| `AB_start:11:00` | -0.8273 | 44 | `no-evidence` | 0.9428 | `positive-edge` |
| `AB_start:10:30` | -0.8545 | 55 | `no-evidence` | 0.5368 | `positive-edge` |
| `AB_start:12:00` | -0.9187 | 27 | `no-evidence` | 0.3295 | `positive-edge` |
| `AB_start:12:30` | -1.7833 | 20 | `no-evidence` | 0.2490 | `positive-edge` |

Because every probe candidate failed in-sample, the family was validated on a
larger 240-scenario deterministic sample before any promotion:

| rank | lever | IS ci_low | IS trades | IS verdict | OOS ci_low | OOS verdict |
|---:|---|---:|---:|---|---:|---|
| 1 | `AB_start:11:00` | -0.8154 | 142 | `no-evidence` | -2.9301 | `no-evidence` |
| 2 | `AB_start:12:00` | -0.8707 | 95 | `no-evidence` | -1.9299 | `no-evidence` |
| 3 | `baseline` | -0.8795 | 207 | `no-evidence` | -1.5505 | `no-evidence` |
| 4 | `AB_start:10:30` | -0.8977 | 173 | `no-evidence` | -1.5467 | `no-evidence` |
| 5 | `AB_start:11:30` | -1.0514 | 121 | `no-evidence` | -1.6720 | `no-evidence` |
| 6 | `AB_start:12:30` | -1.1127 | 80 | `no-evidence` | -0.9840 | `no-evidence` |

The broader validation rejected every later-start point. Starting later also
materially reduced trade count, which would slow proof collection without
improving the after-cost lower bound.

Conclusion: do not move `ENTRY_WINDOW_START`. Do not add this to the standard
lever grid unless new live evidence shows a repeatable morning-specific failure
mode beyond the July 6 clean-window sample.

## Max close-to-entry sweep

Hypothesis: some EOD losers may be chased breakouts where the signal bar had
already closed too far above the strategy's intended entry level. A new
disabled-by-default guard, `ENTRY_MAX_CLOSE_TO_ENTRY_PCT`, rejects these only
when set below its off value of `1.0`.

An 80-scenario probe tested caps from 0.1% to 2% at 2 bps/side:

| rank | lever | IS ci_low | IS trades | IS verdict | OOS ci_low | OOS verdict |
|---:|---|---:|---:|---|---:|---|
| 1 | `baseline` | 0.2106 | 120 | `positive-edge` | 0.5601 | `positive-edge` |
| 2 | `T_max_close_to_entry:0.02` | 0.2106 | 120 | `positive-edge` | 0.5601 | `positive-edge` |
| 3 | `T_max_close_to_entry:0.01` | -0.0048 | 118 | `no-evidence` | 0.5601 | `positive-edge` |
| 4 | `T_max_close_to_entry:0.0075` | -0.0815 | 117 | `no-evidence` | 0.5601 | `positive-edge` |
| 5 | `T_max_close_to_entry:0.005` | -0.1044 | 116 | `no-evidence` | 0.5601 | `positive-edge` |
| 6 | `T_max_close_to_entry:0.001` | -0.2998 | 107 | `no-evidence` | 1.6830 | `positive-edge` |
| 7 | `T_max_close_to_entry:0.0025` | -0.3352 | 112 | `no-evidence` | 0.5493 | `positive-edge` |

The 2% cap was baseline-identical on this sample, so it did not add a real
filter. Tighter caps would have blocked some extended entries, but they reduced
the in-sample confidence lower bound below zero and below baseline.

Conclusion: keep `ENTRY_MAX_CLOSE_TO_ENTRY_PCT=1.0` in paper for now. The guard
is implemented and added to the `T_max_close_to_entry` replay grid for future
repeatable retesting, but the July 6 sweep does not justify promotion.

## Regime-filter replay hardening

While looking for broader EOD-loss mitigations, another measurement gap showed
up: the replay lever grid still could not test `ENABLE_REGIME_FILTER` unless
the sampled scenario set happened to contain the `REGIME_SYMBOL` scenario. The
replay CLI now attaches `{REGIME_SYMBOL}_252d.json` daily bars from the scenario
directory as benchmark context when the sampled traded scenarios do not already
carry regime bars. Backfill also writes optional `regime_daily_bars` into new
scenario files, and the single-symbol, portfolio, and split replay paths pass
point-in-time regime slices into `evaluate_cycle`.

Command:

```bash
timeout 900s bash -lc 'set -a; source /etc/alpaca_bot/alpaca-bot.env; set +a; \
  export ENABLE_LIVE_TRADING=false TRADING_MODE=paper PYTHONPATH=src; \
  python3 -m alpaca_bot.replay.cli lever-sweep \
    --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
    --strategy bull_flag \
    --sample-size 40 \
    --sample-seed bull-flag-regime-20260707 \
    --slippage-bps 2 \
    --coarse \
    --top-k 6 \
    --output /tmp/bull_flag_regime_lever_40.md'
```

Result excerpt:

| rank | lever | IS ci_low | IS trades | IS verdict | OOS ci_low | OOS verdict |
|---:|---|---:|---:|---|---:|---|
| 1 | `N_max_stop:max_stop_pct=0.04` | 3.9630 | 35 | `positive-edge` | 2.4252 | `positive-edge` |
| 2 | `baseline` | 3.3820 | 35 | `positive-edge` | 1.6247 | `positive-edge` |
| 18 | `F_regime:on` | 0.9745 | 20 | `positive-edge` | n/a | n/a |

The regime row was no longer baseline-identical: it cut trades from 35 to 20
and materially reduced the in-sample lower bound. It was not shortlisted for
OOS on this sample.

Conclusion: replay can now measure the regime filter, but this sample does not
justify enabling it.

## Max-stop validation after regime sweep

The regime-enabled coarse sweep surfaced `MAX_STOP_PCT=0.04` as a small-sample
winner. Because prior loss-control sweeps rejected generic stop tightening, the
candidate received a larger deterministic validation before any paper setting
change.

Command:

```bash
timeout 1200s bash -lc 'set -a; source /etc/alpaca_bot/alpaca-bot.env; set +a; \
  export ENABLE_LIVE_TRADING=false TRADING_MODE=paper PYTHONPATH=src; \
  python3 - <<PY
from argparse import Namespace
from pathlib import Path
from alpaca_bot.config import Settings
from alpaca_bot.replay.cli import _select_scenario_paths, _with_regime_daily_bars_from_dir
from alpaca_bot.replay.lever_sweep import LeverPoint, format_lever_sweep_markdown, run_lever_sweep
from alpaca_bot.replay.runner import ReplayRunner

args = Namespace(
    scenario_dir="/var/lib/alpaca-bot/nightly/scenarios",
    limit=0,
    sample_size=160,
    sample_seed="bull-flag-max-stop-validation-20260707",
)
settings = Settings.from_env()
paths = _select_scenario_paths(args)
scenarios = [ReplayRunner.load_scenario(path) for path in paths]
scenarios = _with_regime_daily_bars_from_dir(scenarios, scenario_dir=Path(args.scenario_dir), settings=settings)
points = [
    LeverPoint("baseline", {}),
    LeverPoint("N_max_stop:max_stop_pct=0.04", {"max_stop_pct": 0.04}),
]
rows = run_lever_sweep(
    scenarios=scenarios,
    base_settings=settings,
    strategy="bull_flag",
    grid=points,
    slippage_bps=2.0,
    walk_forward=True,
    top_k=2,
)
Path("/tmp/bull_flag_max_stop_validation_160.md").write_text(
    format_lever_sweep_markdown(rows, strategy="bull_flag", slippage_bps=2.0)
)
PY'
```

Result:

| lever | IS ci_low | IS trades | IS verdict | OOS ci_low | OOS trades | OOS verdict |
|---|---:|---:|---|---:|---:|---|
| `baseline` | -1.2089 | 189 | `no-evidence` | -0.9399 | 62 | `no-evidence` |
| `N_max_stop:max_stop_pct=0.04` | -1.3667 | 189 | `no-evidence` | -0.8798 | 63 | `no-evidence` |

Conclusion: do not change `MAX_STOP_PCT`. The 40-scenario result was not
durable; the larger validation found no OOS-surviving candidate.

## Recovery timestamp attribution hardening

The proof-window trade rows exposed a data-integrity issue in old dirty-window
evidence: a recovered/carryover fill could write an order `updated_at` earlier
than the local order `created_at` when the broker event timestamp was stale.
That can distort session attribution for proof scoring and make operational
losses look as though they occurred in an earlier session.

Fix: matched trade updates and startup-recovered closed fills now floor the
saved `updated_at` timestamp at the local order's `created_at`. Valid broker
fill times are still preserved when they are not earlier than the local order.

Regression coverage:

```bash
python3 -m pytest \
  tests/unit/test_trade_updates.py::test_trade_update_without_receive_time_does_not_backdate_order_before_created_at \
  tests/unit/test_startup_recovery.py::test_closed_exit_recovery_does_not_backdate_order_before_created_at \
  -q
```

Result: `2 passed`. The full trade-update and startup-recovery files also
passed with `113 passed`.

## Exit-shape diagnostics

The prior sweeps tested several exits, but the live clean-window issue needed a
clearer attribution question: are EOD losses mostly trades that never worked,
or trades that worked and gave back gains? Replay tooling now includes
`exit-diagnostics`, which computes per-trade MFE/MAE from scenario bars and
classifies EOD losses as:

- `no_follow_through`: maximum favorable excursion stayed below the configured
  threshold.
- `gave_back`: the trade had enough favorable excursion and then closed as an
  EOD loss.

Command:

```bash
set -a
source /etc/alpaca_bot/alpaca-bot.env
set +a
python3 -m alpaca_bot.replay.cli exit-diagnostics \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategy bull_flag \
  --sample-size 80 \
  --sample-seed bull-flag-exit-diagnostics-20260707 \
  --slippage-bps 2 \
  --portfolio \
  --max-open-positions 4 \
  --starting-equity 68991.62 \
  --max-rows 20 \
  --output /tmp/bull_flag_exit_diagnostics_80.md \
  --json /tmp/bull_flag_exit_diagnostics_80.json
```

Result:

| metric | value |
|---|---:|
| scenarios | 80 |
| closed trades | 52 |
| EOD losses | 12 |
| EOD loss share of losses | 92.31% |
| no-follow-through EOD losses | 6 (50.00%) |
| gave-back EOD losses | 6 (50.00%) |
| median EOD-loss MFE | 0.26% |
| median EOD-loss giveback | 1.73% |

Worst rows:

| symbol | exit session | pnl | return | MFE | MAE | giveback | label |
|---|---:|---:|---:|---:|---:|---:|---|
| `COST` | 2026-01-08 | -14.00 | -1.50% | 0.08% | -1.77% | 1.58% | `no_follow_through` |
| `MIR` | 2025-10-08 | -10.40 | -2.63% | 0.34% | -2.94% | 2.98% | `gave_back` |
| `WULF` | 2025-09-11 | -9.33 | -2.36% | 2.12% | -2.43% | 4.48% | `gave_back` |
| `VCTR` | 2026-05-29 | -8.31 | -1.38% | 0.25% | -1.36% | 1.63% | `no_follow_through` |

Conclusion: the EOD-loss blocker is not one single shape. Half of the sample
never followed through, and half had enough MFE to suggest a giveback/profit
protection rule. Do not promote an exit from this diagnostic alone. Use it to
target the next validation at separated no-follow-through and giveback rules
instead of broad EOD flatten-time changes.

## Giveback exit implementation and validation

Implemented an off-by-default `giveback_exit` rule for long stock positions:
after a trade reaches a configured favorable excursion, emit an exit if the
latest close falls back to or below a configured return threshold. This is
separate from `no_follow_through`: no-follow handles trades that never worked,
giveback handles trades that worked and then rolled over.

New settings:

- `ENABLE_GIVEBACK_EXIT=false`
- `GIVEBACK_EXIT_MIN_FAVORABLE_PCT=0.0025`
- `GIVEBACK_EXIT_MAX_RETURN_PCT=0.0`

The settings are wired through `Settings`, `deploy/compose.yaml`,
`deploy/paper.env.example`, and `scripts/init_server.sh`, but remain disabled
in the paper posture.

Portfolio-scored validation, same 80-scenario sample for both rows:

```bash
set -a
source /etc/alpaca_bot/alpaca-bot.env
set +a
python3 -m alpaca_bot.replay.cli lever-sweep \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategy bull_flag \
  --sample-size 80 \
  --sample-seed bull-flag-giveback-exit-20260707 \
  --slippage-bps 2 \
  --portfolio \
  --max-open-positions 4 \
  --starting-equity 68991.62 \
  --top-k 2 \
  --lever-label V_giveback_exit:on@0.0025,max_return=0 \
  --output /tmp/bull_flag_giveback_exit_portfolio_80.md
```

Result:

| lever | IS ci_low | IS trades | IS verdict | OOS ci_low | OOS trades | OOS verdict |
|---|---:|---:|---|---:|---:|---|
| `baseline` | -2.0313 | 79 | `no-evidence` | -1.3030 | 18 | `no-evidence` |
| `V_giveback_exit:on@0.0025,max_return=0` | -1.1263 | 79 | `no-evidence` | -1.9133 | 18 | `no-evidence` |
| `V_giveback_exit:on@0.005,max_return=0.001` | -1.6263 | 79 | `no-evidence` | -2.1235 | 18 | `no-evidence` |

Conclusion: do not enable `ENABLE_GIVEBACK_EXIT` in paper. The looser variant
improved in-sample but failed OOS; the stricter variant failed both and was
worse than baseline OOS. Keep the implementation and grid coverage as research
tooling, but promotion requires a later independent sample with positive OOS
evidence.

## Combined no-follow-through plus giveback validation

Because exit-shape diagnostics split EOD losses evenly between
`no_follow_through` and `gave_back`, the next check tested whether the two
disabled exit policies only help when paired. The validation used portfolio
scoring, `max_open_positions=4`, `$68,991.62` starting equity, and 2 bps/side
slippage.

First pass, 80 scenarios:

| lever | IS ci_low | IS trades | IS verdict | OOS ci_low | OOS verdict |
|---|---:|---:|---|---:|---|
| `baseline` | -1.8895 | 52 | `no-evidence` | -2.8673 | `no-evidence` |
| `W_combined_no_follow_90m_giveback_0.0025` | -0.3715 | 52 | `no-evidence` | -1.4710 | `no-evidence` |
| `W_combined_no_follow_60m_giveback_0.0025` | -0.3925 | 52 | `no-evidence` | -1.5403 | `no-evidence` |
| `V_giveback_exit:on@0.0025,max_return=0` | -1.0244 | 52 | `no-evidence` | -1.4710 | `no-evidence` |
| `Q_no_follow_through:90m@0.0025` | -1.3273 | 52 | `no-evidence` | -2.8673 | `no-evidence` |

The combined policies improved the lower bound versus baseline but still did
not hold a non-negative OOS lower bound. The best row received a larger
validation.

Second pass, 160 scenarios:

| lever | IS ci_low | IS trades | IS verdict | OOS ci_low | OOS verdict |
|---|---:|---:|---|---:|---|
| `baseline` | -0.1830 | 112 | `no-evidence` | -1.6361 | `no-evidence` |
| `W_combined_no_follow_90m_giveback_0.0025` | -0.5005 | 112 | `no-evidence` | -1.3767 | `no-evidence` |

Conclusion: do not enable combined exits in paper. The 80-scenario improvement
did not survive the larger validation, and OOS evidence remained negative.

## Early-loss exit implementation and validation

The live clean-window DDOG/PANW EOD losses were both `no_follow_through`-shaped:
DDOG reached only `0.09%` MFE before closing `-1.81%`, while PANW never traded
above the paper fill and closed `-2.72%`. A narrower follow-up tested whether
cutting only materially red early trades would avoid the broader no-follow
rule's OOS weakness.

Implemented an off-by-default long-stock `early_loss_exit`: after a configured
hold time, exit only if the latest close is down by at least the configured
return threshold. Defaults remain disabled:

- `ENABLE_EARLY_LOSS_EXIT=false`
- `EARLY_LOSS_EXIT_MINUTES=0`
- `EARLY_LOSS_EXIT_RETURN_PCT=0.01`

Portfolio-scored 80-scenario prefilter, `max_open_positions=4`, `$68,991.62`
starting equity, and 2 bps/side slippage:

| lever | IS ci_low | IS trades | IS verdict | OOS ci_low | OOS verdict |
|---|---:|---:|---|---:|---|
| `AF_early_loss_exit:30m@0.005` | -0.8651 | 51 | `no-evidence` | -1.6443 | `no-evidence` |
| `AF_early_loss_exit:45m@0.005` | -1.3289 | 51 | `no-evidence` | -1.6443 | `no-evidence` |
| `AF_early_loss_exit:60m@0.01` | -1.8448 | 51 | `no-evidence` | -1.2191 | `no-evidence` |
| `baseline` | -2.1706 | 51 | `no-evidence` | -2.8388 | `no-evidence` |

Conclusion: do not enable `ENABLE_EARLY_LOSS_EXIT` in paper. The 30-minute
variant improved IS/OOS lower bounds versus baseline, but every row still had
negative IS and OOS confidence lower bounds. Keep the implementation and grid
coverage as a diagnostic lever only.

Proof-status hardening: `giveback_exit` and `early_loss_exit` are now counted
as legitimate strategy exits for operational-exit-loss attribution. Otherwise,
a future disabled-to-enabled validation experiment that exited at a loss for a
known strategy reason could be mislabeled as an operational exit failure.

## Regime filter portfolio validation

The earlier coarse regime sweep proved replay can measure `ENABLE_REGIME_FILTER`,
but it did not validate the filter in the live paper scoring frame. A focused
portfolio run tested `F_regime:on` against the current paper posture:
cross-sectional top-K replay, `MAX_OPEN_POSITIONS=4`, `$68,991.62` starting
equity, and 2 bps/side slippage.

Command:

```bash
set -a
source /etc/alpaca_bot/alpaca-bot.env
set +a
export ENABLE_LIVE_TRADING=false TRADING_MODE=paper PYTHONPATH=src
python3 -m alpaca_bot.replay.cli lever-sweep \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategy bull_flag \
  --sample-size 160 \
  --sample-seed bull-flag-regime-portfolio-validation-20260707 \
  --slippage-bps 2 \
  --portfolio \
  --max-open-positions 4 \
  --starting-equity 68991.62 \
  --lever-label F_regime:on \
  --top-k 2 \
  --output /tmp/bull_flag_regime_portfolio_validation_160.md
```

Result:

| lever | IS ci_low | IS trades | IS verdict | OOS ci_low | OOS trades | OOS verdict |
|---|---:|---:|---|---:|---:|---|
| `baseline` | -0.0831 | 87 | `no-evidence` | -3.8366 | 33 | `no-evidence` |
| `F_regime:on` | -0.9829 | 63 | `no-evidence` | -3.5278 | 24 | `no-evidence` |

Conclusion: do not enable `ENABLE_REGIME_FILTER`. The filter cut trade count,
worsened the in-sample confidence lower bound, and still did not hold a
non-negative OOS lower bound. The current scenario bundle only contains `SPY`
context, so VIX and sector gates remain unvalidated rather than rejected.

## VIX and sector replay context support

The live engine already supports `ENABLE_VIX_FILTER` and
`ENABLE_SECTOR_FILTER`, but replay could not previously score those filters in
the paper approval frame because `ReplayScenario` only carried regime daily
bars. That made VIX/sector gates blind toggles: they could not be promoted or
rejected with the same OOS discipline as other levers.

Tooling fix:

- `ReplayScenario` now supports optional `vix_daily_bars` and
  `sector_daily_bars_by_etf`.
- Backfill embeds the VIX proxy and sector ETF daily bars into each generated
  scenario when those series are available.
- Single-symbol and shared-equity portfolio replay compute the same
  `MarketContext` as live runtime from point-in-time daily slices only.
- The IS/OOS splitter preserves warmup for VIX and sector context, matching the
  regime-bar treatment.
- Lever grids expose `AC_vix`, `AD_sector`, and `AE_vix_sector` only when the
  loaded scenarios can actually supply those context bars.

Validation:

```bash
pytest tests/unit/test_backfill_fetcher.py \
  tests/unit/test_replay_point_in_time.py \
  tests/unit/test_portfolio_runner.py \
  tests/unit/test_replay_splitter.py \
  tests/unit/test_lever_sweep.py
```

Result: `64 passed`.

Conclusion: this closes a measurement gap; it does not approve a paper posture
change. At this point the live `/var/lib/alpaca-bot/nightly/scenarios` bundle
still lacked VIX/sector context, so a separate current-bundle enrichment step
was needed before the gates could be validated.

## Current-bundle VIX and sector enrichment

To avoid waiting for the next full nightly backfill, a context-only enrichment
path was added to `alpaca-bot-backfill`. It fetches only regime, VIX proxy, and
sector ETF daily bars, then atomically enriches existing scenario JSON files
without replacing their own daily or intraday evidence.

The current scenario bundle was enriched in place via the nightly container so
root-owned `/data/scenarios` permissions were preserved:

```text
enriched_files=999
regime_counts=[267] vix_counts=[267] sector_counts=[11]
sample=AAL_252d.json,AAMI_252d.json,AAOI_252d.json,AAON_252d.json,AAPL_252d.json
```

A follow-up permission audit confirmed enriched files remained host-readable;
sample `AAPL_252d.json` had `267` regime bars, `267` VIX bars, `11` sector ETF
series, and mode `0644`.

## VIX and sector portfolio screen

With the current bundle now context-rich, the market-context gates were tested
in the live paper scoring frame: cross-sectional top-K portfolio replay,
`MAX_OPEN_POSITIONS=4`, `$68,991.62` starting equity, and 2 bps/side slippage.

Command:

```bash
python3 -m alpaca_bot.replay.cli lever-sweep \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategy bull_flag \
  --sample-size 80 \
  --sample-seed bull-flag-market-context-prefilter-20260707 \
  --slippage-bps 2 \
  --portfolio \
  --max-open-positions 4 \
  --starting-equity 68991.62 \
  --lever-label AC_vix:on \
  --lever-label AD_sector:on \
  --lever-label AE_vix_sector:vix=on,sector=on \
  --top-k 4 \
  --output /tmp/bull_flag_market_context_prefilter_80.md
```

Result:

| lever | IS ci_low | IS trades | IS verdict | OOS ci_low | OOS verdict |
|---|---:|---:|---|---:|---|
| `AD_sector:on` | -1.2214 | 36 | `no-evidence` | -4.4885 | `no-evidence` |
| `baseline` | -1.2817 | 57 | `no-evidence` | -5.6149 | `no-evidence` |
| `AE_vix_sector:vix=on,sector=on` | -1.3547 | 33 | `no-evidence` | -4.4885 | `no-evidence` |
| `AC_vix:on` | -2.2704 | 45 | `no-evidence` | -5.6149 | `no-evidence` |

Conclusion: do not enable `ENABLE_VIX_FILTER` or `ENABLE_SECTOR_FILTER` in
paper. `AD_sector:on` was the least-bad row, but it still had negative IS and
OOS lower bounds while cutting trade count from `57` to `36`.

## Entry order active-bars diagnostic

Live proof showed low raw entry fill-throughput: many accepted stop-limit
entries were canceled after the one-bar execution window. To test whether the
one-bar lifetime was too strict, an off-default `ENTRY_ORDER_ACTIVE_BARS`
diagnostic was added. The default remains `1`; live expiry and replay both cap
the order window at `FLATTEN_TIME`.

Current paper-frame sweep:

```bash
python3 -m alpaca_bot.replay.cli lever-sweep \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategy bull_flag \
  --sample-size 80 \
  --sample-seed bull-flag-entry-order-active-bars-prefilter-20260707 \
  --slippage-bps 2 \
  --portfolio \
  --max-open-positions 4 \
  --starting-equity 68991.62 \
  --lever-label AG_entry_order_active_bars:2 \
  --lever-label AG_entry_order_active_bars:3 \
  --top-k 3 \
  --output /tmp/bull_flag_entry_order_active_bars_prefilter_80.md
```

Result:

| lever | IS ci_low | IS trades | IS mean | IS verdict | OOS ci_low | OOS verdict |
|---|---:|---:|---:|---|---:|---|
| `AG_entry_order_active_bars:3` | -1.5087 | 96 | 0.1245 | `no-evidence` | -2.8916 | `no-evidence` |
| `AG_entry_order_active_bars:2` | -1.8735 | 79 | -0.1106 | `no-evidence` | -2.9119 | `no-evidence` |
| `baseline` | -2.8459 | 59 | -0.4829 | `no-evidence` | -2.8843 | `no-evidence` |

Conclusion: do not promote a longer entry-order lifetime. The lever improved
in-sample trade count and lower bound, but no row survived OOS; the extra fills
did not prove durable edge.

## Capacity follow-up

The direct mitigation for the July 6 DDOG/PANW clean-window loss cluster is
portfolio capacity, not another exit lever. A 2026-07-07 capacity audit promoted
paper proof posture to `MAX_OPEN_POSITIONS=1` after K=1 produced positive-edge
results in both the 160-scenario screen and the independent 240-scenario
validation, with stronger profit factor and confidence lower bounds than K=4.

See `docs/strategy-audit/2026-07-07-bull-flag-capacity-reduction.md`.

## Robust proof-horizon exit revalidation

The K=1 capacity audit fixed clustered exposure, but a stricter proof-horizon
audit showed the next weak point was exit quality: baseline K=1 reached the
trade and active-day sample quickly, yet failed the robust proof gate because
EOD-loss share, profit factor, profit concentration, and positive-P&L blockers
remained too frequent. A new `proof-horizon-sweep` CLI now scores existing
lever points against that same live proof gate instead of only the per-trade
CI/OOS objective.

Screen, 120 scenarios, independent seed
`bull-flag-k1-proof-horizon-exit-sweep-20260707`, K=1, 2 bps/side,
`$68,991.62` equity:

| lever | starts passed | first pass rate | trades | P&L | note |
|---|---:|---:|---:|---:|---|
| `Q_no_follow_through:60m@0.0025` | 35 | 17.54% | 66 | -$28.36 | best screen pass rate, still negative |
| `Q_no_follow_through:90m@0.0025` | 31 | 17.54% | 65 | -$46.75 | weaker than 60m |
| `AF_early_loss_exit:45m@0.005` | 29 | 9.88% | 68 | -$49.93 | removed EOD blocker but lost money |
| `V_giveback_exit:on@0.0025,max_return=0` | 28 | 12.99% | 68 | -$39.19 | candidate; lower EOD blocker |
| baseline | 0 | 0.00% | 65 | -$63.68 | EOD-share dominated |

Validation, 240 scenarios, same seed as the robust K=1 baseline
`bull-flag-k1-proof-horizon-20260707`:

| lever | starts passed | first pass rate | trades | P&L | terminal EOD blockers |
|---|---:|---:|---:|---:|---:|
| `AF_early_loss_exit:45m@0.005` | 196 | 34.04% | 133 | $250.54 | 0 |
| `V_giveback_exit:on@0.0025,max_return=0` | 187 | 11.44% | 139 | $176.36 | 4 |
| `Q_no_follow_through:60m@0.0025` | 160 | 19.15% | 132 | $218.87 | 0 |
| baseline | 0 | 0.00% | 125 | $184.68 | 251 |

Independent validation, 240 scenarios,
`bull-flag-k1-proof-horizon-robust-20260707`:

| lever | starts passed | first pass rate | trades | P&L | terminal EOD blockers |
|---|---:|---:|---:|---:|---:|
| `V_giveback_exit:on@0.0025,max_return=0` | 163 | 30.97% | 141 | $22.66 | 0 |
| `AF_early_loss_exit:45m@0.005` | 114 | 20.78% | 144 | -$51.58 | 0 |
| `Q_no_follow_through:60m@0.0025` | 79 | 1.79% | 133 | -$19.29 | 1 |
| baseline | 0 | 0.00% | 128 | -$34.69 | 246 |

Conclusion: promote only `V_giveback_exit:on@0.0025,max_return=0` for paper
proof. It is the only tested exit lever that both improved robust proof-horizon
pass rates and turned the independent 240-scenario sample positive. Keep
`ENABLE_NO_FOLLOW_THROUGH_EXIT=false` and `ENABLE_EARLY_LOSS_EXIT=false`; their
same-seed improvements did not survive independent P&L validation.
