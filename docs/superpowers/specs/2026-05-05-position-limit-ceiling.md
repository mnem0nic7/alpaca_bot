# Position Limit Ceiling — Spec

**Date:** 2026-05-05

## Problem

Commit `7464bdd` removed the `MAX_OPEN_POSITIONS ≤ 20` validation ceiling and set the production
env file to `MAX_OPEN_POSITIONS=500` for a paper-trading scale experiment. The engine enforces
whatever the setting says, so it correctly entered 67 positions (well within 500). The code default
was later updated to 20 (commit `e78a9fa`), but the env file override was never reverted, leaving
production running with a 500-position limit.

The existing test `test_settings_validates_max_open_positions_upper_bound` was updated when the
ceiling was removed — it now asserts that 500 is *allowed*, which is no longer the intended
behaviour.

## Fix

Two targeted changes:

### 1. Re-add ceiling in `Settings.validate()`

Add a hard upper bound of 50. This allows some growth room above the current target of 20 without
allowing a single-line env-file change to silently permit hundreds of positions.

```python
if self.max_open_positions > 50:
    raise ValueError("MAX_OPEN_POSITIONS must be at most 50")
```

### 2. Update the existing test

`test_settings_validates_max_open_positions_upper_bound` currently asserts that 500 is allowed.
Change it to assert that 51 raises `ValueError` and that 50 is accepted.

## Scope

- Modify: `src/alpaca_bot/config/__init__.py` — add the ceiling check after the existing lower-bound
  check (`if self.max_open_positions < 1:`).
- Modify: `tests/unit/test_momentum_strategy.py` — update
  `test_settings_validates_max_open_positions_upper_bound`.

No migrations, no new env vars.

## Deployment

After deploying the code:

1. Update `/etc/alpaca_bot/alpaca-bot.env`: set `MAX_OPEN_POSITIONS=20` (remove or replace the
   current `500` value).
2. Redeploy: `./scripts/deploy.sh /etc/alpaca_bot/alpaca-bot.env`
3. Run remediation commands (already built) to fix the current state:

```bash
alpaca-bot-admin close-excess --dry-run          # preview which 47 will be closed
alpaca-bot-admin close-excess                    # close 47 excess positions
alpaca-bot-admin cancel-partial-fills            # unblock stop submission for the 20 kept
```

## Safety

- The ceiling is checked at Settings construction time — the supervisor will refuse to start if
  `MAX_OPEN_POSITIONS > 50` appears in the env file, failing loudly rather than silently entering
  hundreds of positions.
- The ceiling of 50 was chosen to give headroom above the current production target (20) while
  blocking the kind of accident that led to this incident. If a deliberate scale-up past 50 is
  needed in the future, it requires a code change, not just an env tweak.
- Existing paper-mode paper-trading experiments that set `MAX_OPEN_POSITIONS=500` will fail at
  startup, which is the intended outcome: scale experiments now require an explicit ceiling
  increase rather than silently re-using a stale env value.
