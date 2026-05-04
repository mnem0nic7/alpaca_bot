# Security Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two confirmed security vulnerabilities: shell injection via unvalidated values in `apply_candidate.sh`, and DB credential leakage in the unauthenticated `/healthz` endpoint.

**Architecture:** Two independent, surgical fixes. Task 1 adds a value allowlist guard in `apply_candidate.sh`. Task 2 replaces `str(exc)` with an opaque static string in the `/healthz` handler and logs server-side. No migrations, no new env vars, no engine changes.

**Tech Stack:** bash, Python, pytest, FastAPI TestClient

---

### Task 1: VALUE validation in apply_candidate.sh

**Files:**
- Modify: `scripts/apply_candidate.sh` (lines 36-59)
- Test: `tests/unit/test_apply_candidate.py`

- [ ] **Step 1: Write the failing test**

Add this test to `tests/unit/test_apply_candidate.py`:

```python
def test_apply_rejects_unsafe_value_in_candidate_env(tmp_path):
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text("BREAKOUT_LOOKBACK_BARS=20\n")
    candidate_env = tmp_path / "candidate.env"
    candidate_env.write_text("BREAKOUT_LOOKBACK_BARS=$(rm -rf /)\n")
    deploy = _make_mock_deploy(tmp_path)
    result = _run_apply(env_file, candidate_env, deploy)
    assert result.returncode != 0
    assert env_file.read_text() == "BREAKOUT_LOOKBACK_BARS=20\n"
    assert not (tmp_path / f"{env_file.name}.deploy_log").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_apply_candidate.py::test_apply_rejects_unsafe_value_in_candidate_env -v`
Expected: FAIL — the script currently exits 0 and modifies the env file

- [ ] **Step 3: Add VALUE validation to apply_candidate.sh**

In the comparison loop (around line 38), add validation as the second guard after the empty-key check:

```bash
# Detect whether any param differs from the current env file value
CHANGED=false
while IFS='=' read -r KEY VALUE; do
    [[ -z "$KEY" ]] && continue
    if [[ ! "$VALUE" =~ ^[A-Za-z0-9._+-]+$ ]]; then
        echo "$LOG_PREFIX ERROR: unsafe value for $KEY — rejecting candidate.env" >&2
        exit 1
    fi
    CURRENT=$(grep "^${KEY}=" "$ENV_FILE" | head -1 | cut -d= -f2- || true)
    if [[ "$CURRENT" != "$VALUE" ]]; then
        CHANGED=true
        echo "$LOG_PREFIX $KEY: ${CURRENT:-(not set)} → $VALUE"
    fi
done <<< "$PARAMS"
```

In the apply loop (around line 52), add the same guard as the second guard:

```bash
# Apply all params (update existing lines; append missing ones)
while IFS='=' read -r KEY VALUE; do
    [[ -z "$KEY" ]] && continue
    if [[ ! "$VALUE" =~ ^[A-Za-z0-9._+-]+$ ]]; then
        echo "$LOG_PREFIX ERROR: unsafe value for $KEY — rejecting candidate.env" >&2
        exit 1
    fi
    if grep -q "^${KEY}=" "$ENV_FILE"; then
        sed -i "s|^${KEY}=.*|${KEY}=${VALUE}|" "$ENV_FILE"
    else
        echo "${KEY}=${VALUE}" >> "$ENV_FILE"
    fi
done <<< "$PARAMS"
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `pytest tests/unit/test_apply_candidate.py -v`
Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/apply_candidate.sh tests/unit/test_apply_candidate.py
git commit -m "fix: validate apply_candidate.sh values to prevent shell injection"
```

---

### Task 2: Opaque error body in /healthz

**Files:**
- Modify: `src/alpaca_bot/web/app.py` (lines 259-267)
- Test: `tests/unit/test_web_app.py` (line 241-254)

- [ ] **Step 1: Update the existing test to assert the opaque body**

In `tests/unit/test_web_app.py`, update `test_healthz_route_returns_503_when_database_fails`:

Change line 254 from:
```python
assert response.json() == {"status": "error", "reason": "db unavailable"}
```

To:
```python
assert response.json() == {"status": "error", "reason": "service unavailable"}
assert "db unavailable" not in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_web_app.py::test_healthz_route_returns_503_when_database_fails -v`
Expected: FAIL — current code returns "db unavailable" in reason

- [ ] **Step 3: Fix the healthz handler in app.py**

Change lines 259-267 in `src/alpaca_bot/web/app.py` from:

```python
@app.get("/healthz")
def healthz() -> JSONResponse:
    try:
        health_snapshot = _load_health(app)
    except Exception as exc:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "error", "reason": str(exc)},
        )
```

To:

```python
@app.get("/healthz")
def healthz() -> JSONResponse:
    try:
        health_snapshot = _load_health(app)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("/healthz: health check failed")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "error", "reason": "service unavailable"},
        )
```

- [ ] **Step 4: Run healthz tests to verify all pass**

Run: `pytest tests/unit/test_web_app.py -v -k healthz`
Expected: all healthz tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/web/app.py tests/unit/test_web_app.py
git commit -m "fix: return opaque error body in /healthz to prevent DB credential leakage"
```

---

### Task 3: Final regression check

- [ ] **Step 1: Run full test suite**

Run: `pytest -q --tb=short`
Expected: all tests pass (≥1111 tests)

- [ ] **Step 2: Verify scripts still work**

```bash
# Verify apply_candidate.sh accepts valid numeric values (manual smoke test)
echo "BREAKOUT_LOOKBACK_BARS=25" > /tmp/test_candidate.env
echo "BREAKOUT_LOOKBACK_BARS=20" > /tmp/test_env.env
# Should exit 0 and report param change
bash scripts/apply_candidate.sh /tmp/test_env.env /tmp/test_candidate.env /bin/true
# Verify the env file was updated
grep BREAKOUT_LOOKBACK_BARS /tmp/test_env.env
# Expected: BREAKOUT_LOOKBACK_BARS=25
```
