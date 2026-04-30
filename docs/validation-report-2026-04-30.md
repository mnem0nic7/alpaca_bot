# Strategy Validation Report

Generated: 2026-04-30T18:40:42Z

## Strategy Comparison (all 252d scenarios)

### AAPL
```
strategy,total_trades,win_rate,mean_return_pct,max_drawdown_pct,sharpe_ratio
breakout,22,0.3181818181818182,-0.0020050976069800426,0.0029899318600635307,-0.21929819914208534
momentum,27,0.4444444444444444,-0.00011462454871630004,0.0023342000000002735,-0.012158721829203335
orb,64,0.484375,0.0006305311939447226,0.0030263682344347,0.03566474991863385
high_watermark,0,,,,
ema_pullback,30,0.4,-0.002616059895414218,0.0032818999999998776,-0.25196954451566705

```

### AMZN
```
strategy,total_trades,win_rate,mean_return_pct,max_drawdown_pct,sharpe_ratio
breakout,0,,,,
momentum,0,,,,
orb,0,,,,
high_watermark,0,,,,
ema_pullback,0,,,,

```

### IWM
```
strategy,total_trades,win_rate,mean_return_pct,max_drawdown_pct,sharpe_ratio
breakout,0,,,,
momentum,0,,,,
orb,0,,,,
high_watermark,0,,,,
ema_pullback,0,,,,

```

### META
```
strategy,total_trades,win_rate,mean_return_pct,max_drawdown_pct,sharpe_ratio
breakout,0,,,,
momentum,0,,,,
orb,0,,,,
high_watermark,0,,,,
ema_pullback,0,,,,

```

### MSFT
```
strategy,total_trades,win_rate,mean_return_pct,max_drawdown_pct,sharpe_ratio
breakout,0,,,,
momentum,0,,,,
orb,0,,,,
high_watermark,0,,,,
ema_pullback,0,,,,

```

### NVDA
```
strategy,total_trades,win_rate,mean_return_pct,max_drawdown_pct,sharpe_ratio
breakout,0,,,,
momentum,0,,,,
orb,0,,,,
high_watermark,0,,,,
ema_pullback,0,,,,

```

### QQQ
```
strategy,total_trades,win_rate,mean_return_pct,max_drawdown_pct,sharpe_ratio
breakout,0,,,,
momentum,0,,,,
orb,0,,,,
high_watermark,0,,,,
ema_pullback,0,,,,

```

### SPY
```
strategy,total_trades,win_rate,mean_return_pct,max_drawdown_pct,sharpe_ratio
breakout,0,,,,
momentum,0,,,,
orb,0,,,,
high_watermark,0,,,,
ema_pullback,0,,,,

```

## Parameter Sweep — Breakout (all 252d scenarios)

```

=== AAPL_252d.json ===
  Rank     Score  Trades   MeanRet  Params
  1      -0.0928      24    -0.00%  BREAKOUT_LOOKBACK_BARS=15 RELATIVE_VOLUME_THRESHOLD=1.5 DAILY_SMA_PERIOD=10
  2      -0.0928      24    -0.00%  BREAKOUT_LOOKBACK_BARS=15 RELATIVE_VOLUME_THRESHOLD=1.5 DAILY_SMA_PERIOD=20
  3      -0.0928      24    -0.00%  BREAKOUT_LOOKBACK_BARS=15 RELATIVE_VOLUME_THRESHOLD=1.5 DAILY_SMA_PERIOD=30
  4      -0.0956      30    -0.00%  BREAKOUT_LOOKBACK_BARS=15 RELATIVE_VOLUME_THRESHOLD=1.3 DAILY_SMA_PERIOD=10
  5      -0.0956      30    -0.00%  BREAKOUT_LOOKBACK_BARS=15 RELATIVE_VOLUME_THRESHOLD=1.3 DAILY_SMA_PERIOD=20
  6      -0.0956      30    -0.00%  BREAKOUT_LOOKBACK_BARS=15 RELATIVE_VOLUME_THRESHOLD=1.3 DAILY_SMA_PERIOD=30
  7      -0.1487      17    -0.00%  BREAKOUT_LOOKBACK_BARS=30 RELATIVE_VOLUME_THRESHOLD=1.5 DAILY_SMA_PERIOD=10
  8      -0.1487      17    -0.00%  BREAKOUT_LOOKBACK_BARS=30 RELATIVE_VOLUME_THRESHOLD=1.5 DAILY_SMA_PERIOD=20
  9      -0.1487      17    -0.00%  BREAKOUT_LOOKBACK_BARS=30 RELATIVE_VOLUME_THRESHOLD=1.5 DAILY_SMA_PERIOD=30
  10     -0.1598      20    -0.00%  BREAKOUT_LOOKBACK_BARS=15 RELATIVE_VOLUME_THRESHOLD=1.8 DAILY_SMA_PERIOD=10

=== AAPL_30d.json ===
  Rank     Score  Trades   MeanRet  Params
  1       0.0950       3     0.00%  BREAKOUT_LOOKBACK_BARS=15 RELATIVE_VOLUME_THRESHOLD=1.3 DAILY_SMA_PERIOD=10
  2       0.0950       3     0.00%  BREAKOUT_LOOKBACK_BARS=15 RELATIVE_VOLUME_THRESHOLD=1.3 DAILY_SMA_PERIOD=20
  3       0.0950       3     0.00%  BREAKOUT_LOOKBACK_BARS=15 RELATIVE_VOLUME_THRESHOLD=1.3 DAILY_SMA_PERIOD=30
  4       0.0950       3     0.00%  BREAKOUT_LOOKBACK_BARS=20 RELATIVE_VOLUME_THRESHOLD=1.3 DAILY_SMA_PERIOD=10
  5       0.0950       3     0.00%  BREAKOUT_LOOKBACK_BARS=20 RELATIVE_VOLUME_THRESHOLD=1.3 DAILY_SMA_PERIOD=20
  6       0.0950       3     0.00%  BREAKOUT_LOOKBACK_BARS=20 RELATIVE_VOLUME_THRESHOLD=1.3 DAILY_SMA_PERIOD=30
  7      -0.1156       3    -0.00%  BREAKOUT_LOOKBACK_BARS=15 RELATIVE_VOLUME_THRESHOLD=1.5 DAILY_SMA_PERIOD=10
  8      -0.1156       3    -0.00%  BREAKOUT_LOOKBACK_BARS=15 RELATIVE_VOLUME_THRESHOLD=1.5 DAILY_SMA_PERIOD=20
  9      -0.1156       3    -0.00%  BREAKOUT_LOOKBACK_BARS=15 RELATIVE_VOLUME_THRESHOLD=1.5 DAILY_SMA_PERIOD=30
  10     -0.1156       3    -0.00%  BREAKOUT_LOOKBACK_BARS=20 RELATIVE_VOLUME_THRESHOLD=1.5 DAILY_SMA_PERIOD=10

=== AMZN_252d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== AMZN_30d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== IWM_252d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== IWM_30d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== META_252d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== META_30d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== MSFT_252d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== MSFT_30d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== NVDA_252d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== NVDA_30d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== QQQ_252d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== QQQ_30d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== SPY_252d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== SPY_30d.json ===
  No scored candidates (all disqualified — fewer than min_trades).
```

## Parameter Sweep — Momentum (all 252d scenarios)

```

=== AAPL_252d.json ===
  Rank     Score  Trades   MeanRet  Params
  1       0.6363      13     0.00%  BREAKOUT_LOOKBACK_BARS=15 RELATIVE_VOLUME_THRESHOLD=2.0 DAILY_SMA_PERIOD=20
  2       0.6363      13     0.00%  BREAKOUT_LOOKBACK_BARS=20 RELATIVE_VOLUME_THRESHOLD=2.0 DAILY_SMA_PERIOD=20
  3       0.6363      13     0.00%  BREAKOUT_LOOKBACK_BARS=25 RELATIVE_VOLUME_THRESHOLD=2.0 DAILY_SMA_PERIOD=20
  4       0.6363      13     0.00%  BREAKOUT_LOOKBACK_BARS=30 RELATIVE_VOLUME_THRESHOLD=2.0 DAILY_SMA_PERIOD=20
  5       0.6244      14     0.00%  BREAKOUT_LOOKBACK_BARS=15 RELATIVE_VOLUME_THRESHOLD=2.0 DAILY_SMA_PERIOD=30
  6       0.6244      14     0.00%  BREAKOUT_LOOKBACK_BARS=20 RELATIVE_VOLUME_THRESHOLD=2.0 DAILY_SMA_PERIOD=30
  7       0.6244      14     0.00%  BREAKOUT_LOOKBACK_BARS=25 RELATIVE_VOLUME_THRESHOLD=2.0 DAILY_SMA_PERIOD=30
  8       0.6244      14     0.00%  BREAKOUT_LOOKBACK_BARS=30 RELATIVE_VOLUME_THRESHOLD=2.0 DAILY_SMA_PERIOD=30
  9       0.5956      12     0.00%  BREAKOUT_LOOKBACK_BARS=15 RELATIVE_VOLUME_THRESHOLD=2.0 DAILY_SMA_PERIOD=10
  10      0.5956      12     0.00%  BREAKOUT_LOOKBACK_BARS=20 RELATIVE_VOLUME_THRESHOLD=2.0 DAILY_SMA_PERIOD=10

=== AAPL_30d.json ===
  Rank     Score  Trades   MeanRet  Params
  1       0.5866       3     0.00%  BREAKOUT_LOOKBACK_BARS=15 RELATIVE_VOLUME_THRESHOLD=1.3 DAILY_SMA_PERIOD=30
  2       0.5866       3     0.00%  BREAKOUT_LOOKBACK_BARS=20 RELATIVE_VOLUME_THRESHOLD=1.3 DAILY_SMA_PERIOD=30
  3       0.5866       3     0.00%  BREAKOUT_LOOKBACK_BARS=25 RELATIVE_VOLUME_THRESHOLD=1.3 DAILY_SMA_PERIOD=30
  4       0.5866       3     0.00%  BREAKOUT_LOOKBACK_BARS=30 RELATIVE_VOLUME_THRESHOLD=1.3 DAILY_SMA_PERIOD=30
  5       0.1130       3     0.00%  BREAKOUT_LOOKBACK_BARS=15 RELATIVE_VOLUME_THRESHOLD=1.5 DAILY_SMA_PERIOD=30
  6       0.1130       3     0.00%  BREAKOUT_LOOKBACK_BARS=20 RELATIVE_VOLUME_THRESHOLD=1.5 DAILY_SMA_PERIOD=30
  7       0.1130       3     0.00%  BREAKOUT_LOOKBACK_BARS=25 RELATIVE_VOLUME_THRESHOLD=1.5 DAILY_SMA_PERIOD=30
  8       0.1130       3     0.00%  BREAKOUT_LOOKBACK_BARS=30 RELATIVE_VOLUME_THRESHOLD=1.5 DAILY_SMA_PERIOD=30
  9       0.1075       4     0.00%  BREAKOUT_LOOKBACK_BARS=15 RELATIVE_VOLUME_THRESHOLD=1.5 DAILY_SMA_PERIOD=10
  10      0.1075       4     0.00%  BREAKOUT_LOOKBACK_BARS=15 RELATIVE_VOLUME_THRESHOLD=1.5 DAILY_SMA_PERIOD=20

=== AMZN_252d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== AMZN_30d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== IWM_252d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== IWM_30d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== META_252d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== META_30d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== MSFT_252d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== MSFT_30d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== NVDA_252d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== NVDA_30d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== QQQ_252d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== QQQ_30d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== SPY_252d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== SPY_30d.json ===
  No scored candidates (all disqualified — fewer than min_trades).
```

## Parameter Sweep — ORB (all 252d scenarios)

```

=== AAPL_252d.json ===
  Rank     Score  Trades   MeanRet  Params
  1       0.0408      59     0.00%  BREAKOUT_LOOKBACK_BARS=15 RELATIVE_VOLUME_THRESHOLD=2.0 DAILY_SMA_PERIOD=10
  2       0.0408      59     0.00%  BREAKOUT_LOOKBACK_BARS=15 RELATIVE_VOLUME_THRESHOLD=2.0 DAILY_SMA_PERIOD=20
  3       0.0408      59     0.00%  BREAKOUT_LOOKBACK_BARS=15 RELATIVE_VOLUME_THRESHOLD=2.0 DAILY_SMA_PERIOD=30
  4       0.0408      59     0.00%  BREAKOUT_LOOKBACK_BARS=20 RELATIVE_VOLUME_THRESHOLD=2.0 DAILY_SMA_PERIOD=10
  5       0.0408      59     0.00%  BREAKOUT_LOOKBACK_BARS=20 RELATIVE_VOLUME_THRESHOLD=2.0 DAILY_SMA_PERIOD=20
  6       0.0408      59     0.00%  BREAKOUT_LOOKBACK_BARS=20 RELATIVE_VOLUME_THRESHOLD=2.0 DAILY_SMA_PERIOD=30
  7       0.0408      59     0.00%  BREAKOUT_LOOKBACK_BARS=25 RELATIVE_VOLUME_THRESHOLD=2.0 DAILY_SMA_PERIOD=10
  8       0.0408      59     0.00%  BREAKOUT_LOOKBACK_BARS=25 RELATIVE_VOLUME_THRESHOLD=2.0 DAILY_SMA_PERIOD=20
  9       0.0408      59     0.00%  BREAKOUT_LOOKBACK_BARS=25 RELATIVE_VOLUME_THRESHOLD=2.0 DAILY_SMA_PERIOD=30
  10      0.0408      59     0.00%  BREAKOUT_LOOKBACK_BARS=30 RELATIVE_VOLUME_THRESHOLD=2.0 DAILY_SMA_PERIOD=10

=== AAPL_30d.json ===
  Rank     Score  Trades   MeanRet  Params
  1       0.1751      15     0.00%  BREAKOUT_LOOKBACK_BARS=15 RELATIVE_VOLUME_THRESHOLD=1.3 DAILY_SMA_PERIOD=10
  2       0.1751      15     0.00%  BREAKOUT_LOOKBACK_BARS=15 RELATIVE_VOLUME_THRESHOLD=1.3 DAILY_SMA_PERIOD=20
  3       0.1751      15     0.00%  BREAKOUT_LOOKBACK_BARS=15 RELATIVE_VOLUME_THRESHOLD=1.3 DAILY_SMA_PERIOD=30
  4       0.1751      15     0.00%  BREAKOUT_LOOKBACK_BARS=20 RELATIVE_VOLUME_THRESHOLD=1.3 DAILY_SMA_PERIOD=10
  5       0.1751      15     0.00%  BREAKOUT_LOOKBACK_BARS=20 RELATIVE_VOLUME_THRESHOLD=1.3 DAILY_SMA_PERIOD=20
  6       0.1751      15     0.00%  BREAKOUT_LOOKBACK_BARS=20 RELATIVE_VOLUME_THRESHOLD=1.3 DAILY_SMA_PERIOD=30
  7       0.1751      15     0.00%  BREAKOUT_LOOKBACK_BARS=25 RELATIVE_VOLUME_THRESHOLD=1.3 DAILY_SMA_PERIOD=10
  8       0.1751      15     0.00%  BREAKOUT_LOOKBACK_BARS=25 RELATIVE_VOLUME_THRESHOLD=1.3 DAILY_SMA_PERIOD=20
  9       0.1751      15     0.00%  BREAKOUT_LOOKBACK_BARS=25 RELATIVE_VOLUME_THRESHOLD=1.3 DAILY_SMA_PERIOD=30
  10      0.1751      15     0.00%  BREAKOUT_LOOKBACK_BARS=30 RELATIVE_VOLUME_THRESHOLD=1.3 DAILY_SMA_PERIOD=10

=== AMZN_252d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== AMZN_30d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== IWM_252d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== IWM_30d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== META_252d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== META_30d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== MSFT_252d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== MSFT_30d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== NVDA_252d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== NVDA_30d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== QQQ_252d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== QQQ_30d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== SPY_252d.json ===
  No scored candidates (all disqualified — fewer than min_trades).

=== SPY_30d.json ===
  No scored candidates (all disqualified — fewer than min_trades).
```

