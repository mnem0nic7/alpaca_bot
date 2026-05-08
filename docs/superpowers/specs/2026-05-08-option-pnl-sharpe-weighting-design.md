# Option PnL → Sharpe Weighting Design

## Problem

Option strategies (`breakout_calls`, `bear_bb_squeeze_down`, etc.) will perpetually carry a Sharpe of 0.0 because `list_trade_pnl_by_strategy` only reads the `orders` table. Option trades live in `option_orders`. The result: option strategies always trade at `CONFIDENCE_FLOOR` (25% of equity) regardless of actual win/loss history.

## Goal

Feed closed option trade PnL into the same Sharpe/weighting pipeline used for equity strategies, so option strategies earn higher confidence scores when they perform well — and are subject to losing streak exclusion when they don't.

## Approach

**Minimal merge at the data collection layer.** The `compute_strategy_weights` function is data-agnostic — it takes `list[dict]` with `strategy_name`, `exit_date`, `pnl`. We add an equivalent query on `option_orders` and concatenate the results before passing them to the weighting algorithm. No changes to the weighting algorithm or scoring logic.

## PnL Formula

Option `fill_price` is stored per share (e.g., $3.00 ask → $300 total per contract). `quantity` is in contracts. Both buy and sell records live in `option_orders`.

```
option_pnl = (sell_fill_price - buy_fill_price) * min(sell_qty, buy_qty) * 100
```

The `* 100` converts per-share option prices to per-contract dollar PnL, producing values comparable in magnitude to equity PnL fed into the same Sharpe formula.

A sell is matched to its entry by `occ_symbol` (which encodes underlying, expiry, type, and strike — uniquely identifies the contract), `strategy_name`, `trading_mode`, and `strategy_version`, using the same correlated-subquery pattern as the equity version.

## Files

### `src/alpaca_bot/storage/repositories.py`

**Add** `OptionOrderRepository.list_trade_pnl_by_strategy(...)` returning `list[dict]` in the same format as `OrderStore.list_trade_pnl_by_strategy`:

```python
{
    "strategy_name": str,
    "exit_date": date,
    "pnl": float,  # (sell_fill - buy_fill) * qty * 100
}
```

Only rows where `side='sell'`, `status='filled'`, and a correlated buy exists with `fill_price IS NOT NULL` are included.

### `src/alpaca_bot/runtime/supervisor.py`

**Two call sites** need the merged list:

1. `_update_session_weights` (line ~1414): after fetching `trade_rows` from `order_store`, fetch `option_trade_rows` from `option_order_store` and concatenate before calling `compute_strategy_weights`.

2. Losing streak calculation (line ~372): same merge before calling `compute_losing_day_streaks`.

In both cases the merge is:
```python
all_trade_rows = trade_rows + option_trade_rows
```

`option_order_store` is accessed via `getattr(self.runtime, "option_order_store", None)` — falls back to `[]` if not wired (keeps tests that don't inject an option store working).

## Data Flow

```
option_orders (sell, filled)
        ↓
OptionOrderRepository.list_trade_pnl_by_strategy()
        ↓
[{strategy_name, exit_date, pnl}, ...]   ← merged with equity rows
        ↓
compute_strategy_weights(all_trade_rows, active_names)
        ↓
WeightResult.sharpes  →  compute_confidence_scores()  →  session_confidence_scores
```

## Error Handling

- If `option_order_store` is `None`: treat as `[]` (no option rows).
- If the option query raises: log and treat as `[]` (equity Sharpe still computed correctly).
- Rows where buy entry has no `fill_price`: excluded by SQL filter (same as equity).

## Test Coverage

### `tests/unit/test_option_order_repository_pnl.py` (new)

- `test_returns_empty_when_no_closed_sells`: no rows → `[]`
- `test_returns_correct_pnl_for_matched_buy_sell`: one buy+sell pair → `pnl = (sell - buy) * qty * 100`
- `test_excludes_unmatched_sells`: sell with no buy entry fill → excluded
- `test_respects_date_range`: sell outside date range → excluded
- `test_respects_trading_mode_and_strategy_version`: cross-mode rows excluded

### `tests/unit/test_supervisor_weights.py` (extend)

- `test_option_pnl_feeds_into_sharpe`: a supervisor with an option strategy having closed profitable trades produces a non-zero Sharpe for that strategy after `_update_session_weights`.

## Constraints

- No schema changes needed — `option_orders` already has `fill_price`, `filled_quantity`, `side`, `status`, `strategy_name`.
- `compute_strategy_weights` requires `min_trades = 5` before computing Sharpe — option strategies need 5+ closed trades before earning a positive Sharpe.
- The `* 100` multiplier means option PnL will tend to be larger in dollar terms than equity PnL for the same win. This is intentional: a profitable option strategy earns Sharpe faster, reflecting the capital efficiency of options.
- Losing streak detection (`compute_losing_day_streaks`) will now apply to option strategies that have enough closed trade history, which is the correct behavior.

## Out of Scope

- Changing how option position sizing works (still uses `RISK_PER_TRADE_PCT`, not capital weight).
- Tracking unrealized option PnL.
- Normalizing option PnL by notional to compare with equity PnL — this is a future consideration.
