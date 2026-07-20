---
name: evostock-status
description: Inspect, diagnose, pause, or resume an EvoStock automation deployment without running investment analysis. Use when the user asks whether EvoStock is configured, which Gmail or executor is active, whether scheduled tasks are healthy, why a run skipped, what is quarantined, or to pause or resume automation safely.
---

# EvoStock Status

Use the configured `PROJECT_ROOT` and `EVOSTOCK_DATA_DIR`, defaulting to `~/.evostock-lab/app` and `~/.evostock-lab/data`.

## Inspect

1. Run `<runtime-python> <PROJECT_ROOT>/scripts/evostockctl.py --data-dir <data-dir> status`.
2. Inspect deployment status, expected Gmail account, broker profile hash, active executor, required task IDs, latest gate events, active broker generation, unresolved quarantine, latest decision, and pending outcomes.
3. When Gmail health is in question, call only `get_profile("me")` first. Compare it with the expected account; do not search the mailbox during a status check.
4. Query the current platform's scheduled-task list and compare real task IDs and enabled states with `deployment.json`. Report drift instead of silently rewriting either side.
5. Never expose OAuth tokens, raw email bodies, private holdings beyond what the user requested, or unverified investment conclusions.

## Pause

Pause every recorded platform task first. Only after all task pauses succeed, run `evostockctl.py pause`. If a platform pause fails, leave deployment state unchanged and report the affected task ID.

## Resume

Run `evostockctl.py status` and require no readiness issues. Enable every recorded task, verify the scheduler state, then run `evostockctl.py resume`. Never resume a deployment with a Gmail mismatch, unverified broker profile, missing task, or unresolved holding-affecting quarantine.
