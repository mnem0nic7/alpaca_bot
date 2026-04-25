---
name: plan-and-refine
description: Use when planning any new feature or fix for this codebase — runs brainstorm → write-plan → grill-me → refine loop before handing off to Feature Developer. Use whenever scoping work, before writing any code, or when the user says "plan", "design", or "let's think through".
---

# Plan and Refine

The full planning loop for this project's agent team. Produces a battle-tested implementation plan before any code is written.

## Flow

```
brainstorming → writing-plans → grill-me → refine → [repeat] → feature-dev
```

Each stage gates the next. Do not skip ahead.

## Stage 1 — Brainstorm

**REQUIRED SUB-SKILL:** Use `superpowers:brainstorming`

Explore intent, constraints, and design. Ends when a spec doc is committed to `docs/superpowers/specs/`.

## Stage 2 — Write Plan

**REQUIRED SUB-SKILL:** Use `superpowers:writing-plans`

Produce a step-by-step implementation plan saved to `docs/superpowers/plans/`. Every task must have exact file paths, complete code, and exact test commands. No placeholders.

## Stage 3 — Grill the Plan

**REQUIRED SUB-SKILL:** Use `grill-me`

After the plan is written, announce:

> "Plan written. Now grilling it — I'll interview you relentlessly until every branch of the design tree is resolved."

Then invoke `grill-me` against the plan. Ask questions one at a time, provide your recommended answer for each, and explore the codebase instead of asking when the answer is discoverable there.

### Domain-specific questions to prioritise for this codebase

Cover all that apply before declaring the grilling done:

**Financial safety**
- Does this change affect order submission, position sizing, or stop placement? If so, what's the worst-case loss if it fires with stale data?
- Is there a scenario where two concurrent cycles could submit conflicting orders?

**Audit trail**
- Does every state change append an `AuditEvent`? If a crash happens mid-operation, can the next startup recover without silent data loss?

**Intent / dispatch separation**
- Does this change respect the two-phase design (intents written to Postgres first, dispatched separately)? Or does it bypass the queue?

**Postgres advisory lock**
- Could this change allow a second supervisor instance to run alongside the first?

**Pure engine boundary**
- Does `evaluate_cycle()` remain a pure function after this change? Any I/O introduced inside the engine must be justified.

**Rollback safety**
- If the migration fails mid-way, is the database left in a valid state? Is it reversible?

**Test coverage**
- Is there a test that would catch the most dangerous regression this change could introduce? Does it exist yet?

**Paper vs. live mode safety**
- Does this change behave identically in `TRADING_MODE=paper` and `TRADING_MODE=live`? If not, what prevents a misconfigured env from sending real orders?
- Does `ENABLE_LIVE_TRADING=false` remain an effective gate after this change?

**Market-hours guards**
- Can this change submit or modify orders outside `ENTRY_WINDOW_START`/`ENTRY_WINDOW_END`?
- Does any new code path hit the broker when `_market_is_open()` returns False?
- Is the fallback safe when the broker clock is unavailable (current default: assumes open)?

**Environment variables and credentials**
- Does this change introduce any new env vars? Are they validated in `Settings.from_env()` or could they silently fall back to an insecure default?
- Could a missing or misspelled env var cause this change to use wrong credentials or skip a safety gate?

## Stage 4 — Refine

Update the plan (and spec if needed) based on answers from the grilling. Commit the updated files.

If the grilling exposed a structural flaw (wrong abstraction, missing audit event, unsafe dispatch path), return to Stage 2 and rewrite the affected tasks. Do not patch over a structural problem with a comment.

## Stage 5 — Second Grilling (if needed)

If Stage 4 changed more than one task, run `grill-me` again on the revised plan. Stop when a full grilling round produces no plan changes.

## Stage 6 — Hand off

Announce the plan is ready, then invoke:

**REQUIRED SUB-SKILL:** Use `feature-dev:feature-dev`

Pass the path to the refined plan file as context.

## When to stop grilling

Stop when:
- All domain-specific questions above have been addressed
- No answer required a task change
- The plan has been revised and re-grilled at least once

Do NOT stop because:
- The plan feels good
- The user seems impatient (flag the remaining open questions instead)
- You've asked five questions and nothing broke
