# Per-Trade Dollar Loss Cap Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

## Goal

Add a `MAX_LOSS_PER_TRADE_DOLLARS` config option that caps the maximum dollar loss any single trade can produce on a clean stop-out. This gives the operator an intuitive, absolute dollar-denominated guardrail to complement the existing percentage-based `RISK_PER_TRADE_PCT`.

## Why Tightening the Stop Alone Doesn't Help

The sizing engine in `risk/sizing.py` back-calculates quantity from the risk budget:

```
quantity = (equity × risk_per_trade_pct) / (entry_price − stop_price)
```

Tightening the stop reduces `(entry_price − stop_price)`, which *increases* quantity by the same factor. A clean stop-out still loses `equity × risk_per_trade_pct` — the same dollar amount. The only levers that directly cap per-trade dollar loss are:

1. **Reduce `RISK_PER_TRADE_PCT`** — already exists, percentage-based, scales with equity
2. **Add `MAX_LOSS_PER_TRADE_DOLLARS`** — new, absolute dollar cap, does not scale with equity growth

The new setting is complementary: `RISK_PER_TRADE_PCT` remains the primary control; the dollar cap is a hard ceiling for operators who think in dollar terms rather than account percentage terms.

## Architecture

**Files to modify:**

- `src/alpaca_bot/config/__init__.py` — new optional field `max_loss_per_trade_dollars: float | None`, parsing from `MAX_LOSS_PER_TRADE_DOLLARS` env var, validation (must be > 0 when set)
- `src/alpaca_bot/risk/sizing.py` — after computing quantity from risk budget, apply dollar cap: `quantity = min(quantity, max_loss_dollars / risk_per_share)`

**Files to test:**

- `tests/unit/test_sizing.py` — existing file; add cases for dollar cap binding, dollar cap non-binding, dollar cap disabled (None)

**No database migrations.** No new audit events beyond existing sizing logic. No changes to order dispatch, stop placement, or the engine's pure-function boundary.

## Detailed Design

### `config/__init__.py`

New field in the `Settings` dataclass (after `min_position_notional`):

```python
max_loss_per_trade_dollars: float | None = None
```

Parsed in `from_env()`:

```python
max_loss_per_trade_dollars=(
    float(values["MAX_LOSS_PER_TRADE_DOLLARS"])
    if "MAX_LOSS_PER_TRADE_DOLLARS" in values
    else None
),
```

Validated in `validate()`:

```python
if self.max_loss_per_trade_dollars is not None and self.max_loss_per_trade_dollars <= 0:
    raise ValueError("MAX_LOSS_PER_TRADE_DOLLARS must be > 0")
```

### `risk/sizing.py`

In `calculate_position_size()`, after the initial quantity calculation and before the `max_position_pct` notional cap:

```python
if settings.max_loss_per_trade_dollars is not None:
    dollar_cap_qty = settings.max_loss_per_trade_dollars / risk_per_share
    quantity = min(quantity, dollar_cap_qty)
    if not fractionable:
        quantity = math.floor(quantity)
    if not fractionable and quantity < 1:
        return 0.0
    if quantity <= 0.0:
        return 0.0
```

This means the final quantity is `min(risk_budget_qty, dollar_cap_qty, notional_cap_qty)` — the tightest constraint wins.

### Interaction with Existing Constraints

| Constraint | When binding |
|---|---|
| `RISK_PER_TRADE_PCT` | Always — sets the base quantity |
| `MAX_LOSS_PER_TRADE_DOLLARS` | When dollar cap < risk_budget for the given stop distance |
| `MAX_POSITION_PCT` | When notional (quantity × entry_price) exceeds the pct of equity |

The dollar cap is most useful when ATR is narrow (tight stop → large quantity → dollar cap cuts quantity down). When ATR is wide (large stop distance → small quantity), the dollar cap is non-binding because the risk budget already limits exposure.

## Backward Compatibility

Default is `None` — disabled. Existing deployments without `MAX_LOSS_PER_TRADE_DOLLARS` in their env file behave identically to today. No migration required.

## Recommended Config Values

For a ~$10K account wanting max $10–15 loss per trade:
```
MAX_LOSS_PER_TRADE_DOLLARS=12
```

This complements `RISK_PER_TRADE_PCT=0.0025` (which budgets $25 at $10K). The dollar cap halves the maximum loss when stops are tighter than ~2% from entry.

## Out of Scope

- Changing stop placement logic (ATR multiplier, breakout buffer) — separate concern
- Per-symbol or per-strategy dollar caps — not needed; all strategies use the same sizing function
- Fractional shares: the cap applies to fractional quantities too; the `not fractionable` floor-check guards against rounding to zero for non-fractional symbols
