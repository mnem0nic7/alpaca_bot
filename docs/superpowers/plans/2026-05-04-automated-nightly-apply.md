# Automated Nightly Parameter Apply — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After `alpaca-bot-nightly` produces a walk-forward held candidate, automatically merge the
3 winning parameters into the system env file and restart the supervisor — no human action required
between nightly training and next-day trading.

**Architecture:** Switch the `nightly` Docker service from a named volume to a host bind mount at
`/var/lib/alpaca-bot/nightly`, making `candidate.env` directly readable. A new
`scripts/apply_candidate.sh` merges changed params and calls `deploy.sh`. The cron job chains apply
after nightly with `&&`.

**Tech Stack:** Bash, `sed`, existing `deploy.sh`. No new Python. No new Docker images. No migrations.

---

## Files Changed

| File | Change |
|---|---|
| `deploy/compose.yaml` | Bind mount instead of named volume for nightly service |
| `scripts/apply_candidate.sh` | New apply script |
| `scripts/init_server.sh` | Add `mkdir -p /var/lib/alpaca-bot/nightly` |
| `deploy/cron.d/alpaca-bot` | Chain apply after nightly |
| `tests/unit/test_apply_candidate.py` | 4 subprocess tests |
| `DEPLOYMENT.md` | Document automated apply flow |

---

### Task 1: Write 4 failing tests for apply_candidate.sh

**Files:**
- Create: `tests/unit/test_apply_candidate.py`

- [ ] **Step 1: Write the test file**

```python
# tests/unit/test_apply_candidate.py
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "apply_candidate.sh"


def _make_mock_deploy(tmp_path: Path) -> Path:
    """Create a mock deploy.sh that appends a sentinel line when called."""
    deploy = tmp_path / "mock_deploy.sh"
    deploy.write_text(
        "#!/usr/bin/env bash\n"
        "echo \"mock-deploy $1\" >> \"${1}.deploy_log\"\n"
    )
    deploy.chmod(deploy.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return deploy


def _run_apply(env_file: Path, candidate_env: Path, deploy_script: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(SCRIPT), str(env_file), str(candidate_env), str(deploy_script)],
        capture_output=True,
        text=True,
    )


def test_apply_no_candidate_env_exits_0_unchanged(tmp_path):
    """No candidate.env → exit 0, env file unchanged, deploy not called."""
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text("BREAKOUT_LOOKBACK_BARS=20\nRELATIVE_VOLUME_THRESHOLD=1.5\n")
    candidate_env = tmp_path / "candidate.env"  # does NOT exist
    mock_deploy = _make_mock_deploy(tmp_path)

    result = _run_apply(env_file, candidate_env, mock_deploy)

    assert result.returncode == 0
    assert env_file.read_text() == "BREAKOUT_LOOKBACK_BARS=20\nRELATIVE_VOLUME_THRESHOLD=1.5\n"
    deploy_log = Path(str(env_file) + ".deploy_log")
    assert not deploy_log.exists(), "deploy.sh must not be called when no candidate.env"


def test_apply_updates_changed_params_and_calls_deploy(tmp_path):
    """Changed params → env file updated with new values, deploy.sh called."""
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text(
        "TRADING_MODE=paper\n"
        "BREAKOUT_LOOKBACK_BARS=20\n"
        "RELATIVE_VOLUME_THRESHOLD=1.5\n"
        "DAILY_SMA_PERIOD=20\n"
    )
    candidate_env = tmp_path / "candidate.env"
    candidate_env.write_text(
        "# Best params from tuning run 2026-05-04T22:30:00Z\n"
        "# Score=0.84  Trades=47  WinRate=68%\n"
        "BREAKOUT_LOOKBACK_BARS=30\n"
        "RELATIVE_VOLUME_THRESHOLD=2.0\n"
        "DAILY_SMA_PERIOD=50\n"
    )
    mock_deploy = _make_mock_deploy(tmp_path)

    result = _run_apply(env_file, candidate_env, mock_deploy)

    assert result.returncode == 0
    updated = env_file.read_text()
    assert "BREAKOUT_LOOKBACK_BARS=30" in updated
    assert "RELATIVE_VOLUME_THRESHOLD=2.0" in updated
    assert "DAILY_SMA_PERIOD=50" in updated
    # Non-param lines untouched
    assert "TRADING_MODE=paper" in updated
    # deploy.sh was called
    deploy_log = Path(str(env_file) + ".deploy_log")
    assert deploy_log.exists(), "deploy.sh must be called when params changed"


def test_apply_no_op_when_params_already_current(tmp_path):
    """Params in candidate match env file → no env change, deploy.sh NOT called."""
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text(
        "BREAKOUT_LOOKBACK_BARS=30\n"
        "RELATIVE_VOLUME_THRESHOLD=2.0\n"
        "DAILY_SMA_PERIOD=50\n"
    )
    candidate_env = tmp_path / "candidate.env"
    candidate_env.write_text(
        "# Same params\n"
        "BREAKOUT_LOOKBACK_BARS=30\n"
        "RELATIVE_VOLUME_THRESHOLD=2.0\n"
        "DAILY_SMA_PERIOD=50\n"
    )
    mock_deploy = _make_mock_deploy(tmp_path)
    original_content = env_file.read_text()

    result = _run_apply(env_file, candidate_env, mock_deploy)

    assert result.returncode == 0
    assert env_file.read_text() == original_content, "env file must not change"
    deploy_log = Path(str(env_file) + ".deploy_log")
    assert not deploy_log.exists(), "deploy.sh must NOT be called when params unchanged"


def test_apply_appends_param_not_in_env_file(tmp_path):
    """A param in candidate.env not present in env file is appended."""
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text("TRADING_MODE=paper\nBREAKOUT_LOOKBACK_BARS=20\n")
    candidate_env = tmp_path / "candidate.env"
    candidate_env.write_text(
        "BREAKOUT_LOOKBACK_BARS=20\n"    # unchanged
        "DAILY_SMA_PERIOD=50\n"          # NEW — not in env file
    )
    mock_deploy = _make_mock_deploy(tmp_path)

    result = _run_apply(env_file, candidate_env, mock_deploy)

    assert result.returncode == 0
    updated = env_file.read_text()
    assert "DAILY_SMA_PERIOD=50" in updated
    # deploy.sh called because DAILY_SMA_PERIOD was new/changed
    deploy_log = Path(str(env_file) + ".deploy_log")
    assert deploy_log.exists(), "deploy.sh must be called when a new param is added"
```

- [ ] **Step 2: Run tests to verify they all fail (script doesn't exist yet)**

```bash
pytest tests/unit/test_apply_candidate.py -v
```

Expected: all 4 FAIL — `OSError: [Errno 8] Exec format error` or permission error because the script file doesn't exist.

---

### Task 2: Create `scripts/apply_candidate.sh`

**Files:**
- Create: `scripts/apply_candidate.sh`

- [ ] **Step 1: Write the script**

```bash
#!/usr/bin/env bash
# Apply nightly candidate params to the system env file and restart the supervisor
# if any params changed.
#
# Usage: apply_candidate.sh [ENV_FILE] [CANDIDATE_ENV] [DEPLOY_SCRIPT]
#   ENV_FILE       System env file (default: /etc/alpaca_bot/alpaca-bot.env)
#   CANDIDATE_ENV  Candidate params file from nightly run (default: /var/lib/alpaca-bot/nightly/candidate.env)
#   DEPLOY_SCRIPT  Script to restart supervisor (default: <repo>/scripts/deploy.sh)
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${1:-/etc/alpaca_bot/alpaca-bot.env}"
CANDIDATE_ENV="${2:-/var/lib/alpaca-bot/nightly/candidate.env}"
DEPLOY_SCRIPT="${3:-$ROOT_DIR/scripts/deploy.sh}"
LOG_PREFIX="[apply_candidate $(date -u '+%Y-%m-%dT%H:%M:%SZ')]"

if [[ ! -f "$CANDIDATE_ENV" ]]; then
    echo "$LOG_PREFIX No candidate.env at $CANDIDATE_ENV — nothing to apply."
    exit 0
fi

if [[ ! -f "$ENV_FILE" ]]; then
    echo "$LOG_PREFIX env file not found: $ENV_FILE" >&2
    exit 1
fi

# Extract non-comment, non-empty KEY=VALUE lines from candidate.env
PARAMS=$(grep -E '^[A-Z_]+=' "$CANDIDATE_ENV") || true

if [[ -z "$PARAMS" ]]; then
    echo "$LOG_PREFIX candidate.env has no param lines — nothing to apply."
    exit 0
fi

# Detect whether any param differs from the current env file value
CHANGED=false
while IFS='=' read -r KEY VALUE; do
    [[ -z "$KEY" ]] && continue
    CURRENT=$(grep "^${KEY}=" "$ENV_FILE" | head -1 | cut -d= -f2-)
    if [[ "$CURRENT" != "$VALUE" ]]; then
        CHANGED=true
        echo "$LOG_PREFIX $KEY: ${CURRENT:-(not set)} → $VALUE"
    fi
done <<< "$PARAMS"

if [[ "$CHANGED" == "false" ]]; then
    echo "$LOG_PREFIX Params unchanged — no restart needed."
    exit 0
fi

# Apply all params (update existing lines; append missing ones)
while IFS='=' read -r KEY VALUE; do
    [[ -z "$KEY" ]] && continue
    if grep -q "^${KEY}=" "$ENV_FILE"; then
        sed -i "s|^${KEY}=.*|${KEY}=${VALUE}|" "$ENV_FILE"
    else
        echo "${KEY}=${VALUE}" >> "$ENV_FILE"
    fi
done <<< "$PARAMS"

echo "$LOG_PREFIX Params applied. Restarting supervisor..."
"$DEPLOY_SCRIPT" "$ENV_FILE"
echo "$LOG_PREFIX Done."
```

- [ ] **Step 2: Make the script executable**

```bash
chmod +x scripts/apply_candidate.sh
```

- [ ] **Step 3: Run the tests to verify they pass**

```bash
pytest tests/unit/test_apply_candidate.py -v
```

Expected: all 4 PASS

- [ ] **Step 4: Commit**

```bash
git add scripts/apply_candidate.sh tests/unit/test_apply_candidate.py
git commit -m "feat: add apply_candidate.sh — merge nightly params and restart supervisor"
```

---

### Task 3: Switch nightly from named volume to bind mount

**Files:**
- Modify: `deploy/compose.yaml`

- [ ] **Step 1: Replace named volume with bind mount**

In `deploy/compose.yaml`, change the top-level `volumes:` block from:

```yaml
volumes:
  postgres_data:
  nightly_data:
```

to:

```yaml
volumes:
  postgres_data:
```

And in the `nightly` service, change:

```yaml
    volumes:
      - nightly_data:/data
```

to:

```yaml
    volumes:
      - /var/lib/alpaca-bot/nightly:/data
```

- [ ] **Step 2: Verify compose file is valid**

```bash
docker compose -f deploy/compose.yaml config --quiet
```

Expected: exit 0 (no syntax errors)

- [ ] **Step 3: Commit**

```bash
git add deploy/compose.yaml
git commit -m "feat: switch nightly service to bind mount at /var/lib/alpaca-bot/nightly"
```

---

### Task 4: Update init_server.sh, cron, and DEPLOYMENT.md

**Files:**
- Modify: `scripts/init_server.sh`
- Modify: `deploy/cron.d/alpaca-bot`
- Modify: `DEPLOYMENT.md`

- [ ] **Step 1: Add bind-mount directory creation to init_server.sh**

In `scripts/init_server.sh`, after the `mkdir -p "$(dirname "$ENV_FILE")"` line, add:

```bash
mkdir -p /var/lib/alpaca-bot/nightly
```

Full context around the change (find the line `mkdir -p "$(dirname "$ENV_FILE")"` and add after it):

```bash
mkdir -p "$(dirname "$ENV_FILE")"
mkdir -p /var/lib/alpaca-bot/nightly
```

- [ ] **Step 2: Extend the cron job**

Replace `deploy/cron.d/alpaca-bot` contents with:

```
# Nightly evolve pipeline — runs 30 min after NYSE close (22:30 UTC = 5:30 PM ET year-round)
# apply_candidate.sh chains after nightly (&&) and restarts the supervisor if params changed.
30 22 * * 1-5 root mkdir -p /var/lib/alpaca-bot/nightly && cd /workspace/alpaca_bot && docker compose -f deploy/compose.yaml run --rm nightly >> /var/log/alpaca-bot-nightly.log 2>&1 && ./scripts/apply_candidate.sh /etc/alpaca_bot/alpaca-bot.env >> /var/log/alpaca-bot-nightly.log 2>&1
```

(One line — the `mkdir -p` guards against missing directory on first run, the `&&` chain ensures
apply only fires if nightly exits 0.)

- [ ] **Step 3: Add automated apply section to DEPLOYMENT.md**

In `DEPLOYMENT.md`, after the "Scheduling" section or before "First-time setup", add:

```markdown
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
```

- [ ] **Step 4: Commit**

```bash
git add scripts/init_server.sh deploy/cron.d/alpaca-bot DEPLOYMENT.md
git commit -m "feat: chain apply_candidate.sh in cron; update init_server.sh and DEPLOYMENT.md"
```

---

### Task 5: Full regression + manual verification

- [ ] **Step 1: Run full test suite**

```bash
pytest -q --tb=short
```

Expected: all tests pass (baseline was 1119 before this feature)

- [ ] **Step 2: Verify apply script help / smoke test**

```bash
# Verify script is executable and runs
./scripts/apply_candidate.sh --help 2>&1 | head -3 || true
# Expected: prints "No candidate.env at /var/lib/alpaca-bot/nightly/candidate.env" and exits 0

# Verify compose file is valid with bind mount
docker compose -f deploy/compose.yaml config --quiet
```

- [ ] **Step 3: Install the updated cron**

```bash
./scripts/install_cron.sh
```

Expected: "Cron installed. Runs weekdays at 22:30 UTC (5:30 PM ET)."

- [ ] **Step 4: Verify the installed cron has the apply chain**

```bash
cat /etc/cron.d/alpaca-bot
```

Expected: single line containing both `run --rm nightly` and `apply_candidate.sh`.

- [ ] **Step 5: Commit spec and plan docs**

```bash
git add docs/superpowers/specs/2026-05-04-automated-nightly-apply.md \
        docs/superpowers/plans/2026-05-04-automated-nightly-apply.md
git commit -m "docs: spec and plan for automated nightly parameter apply"
```
