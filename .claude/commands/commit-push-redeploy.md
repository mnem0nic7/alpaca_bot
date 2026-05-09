---
allowed-tools: Bash(git add:*), Bash(git status:*), Bash(git diff:*), Bash(git commit:*), Bash(git push:*), Bash(./scripts/deploy.sh:*)
description: Stage, commit, push to main, and redeploy
---

## Context

- Current git status: !`git status`
- Current git diff (staged and unstaged changes): !`git diff HEAD`
- Current branch: !`git branch --show-current`

## Your task

Based on the above changes:

1. Stage all modified and untracked files (`git add -A`)
2. Create a single commit with an appropriate message following the project's commit style
3. Push directly to origin main
4. Run `./scripts/deploy.sh /etc/alpaca_bot/alpaca-bot.env` to redeploy
5. You have the capability to call multiple tools in a single response. You MUST do all of the above in a single message. Do not use any other tools or do anything else. Do not send any other text or messages besides these tool calls.
