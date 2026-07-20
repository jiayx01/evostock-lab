---
name: evostock-run
description: Execute a scheduled or manual EvoStock portfolio workflow using Gmail-derived holdings, XNYS time gates, atomic ledgers, independent agent review, idempotent email delivery, and no-lookahead outcome learning. Use for EvoStock intraday, daily-review, post-close, weekly-review, or monthly-review runs. Never use it to place trades or bypass a failed identity, schedule, ledger, or evidence gate.
---

# EvoStock Run

Execute one mode from a scheduled task or an explicit manual request. Treat `dry_run=true` as no-send and no-production-decision mode.

## Stage 0

Obtain `mode`, `EVOSTOCK_DATA_DIR`, `PROJECT_ROOT`, and the runtime Python from the scheduled prompt or deployment state. Before reading investment prompts, memory, Gmail, holdings, or reports, run only:

```bash
<runtime-python> <PROJECT_ROOT>/automation_gate.py \
  --mode <mode> \
  --append-event-log <data-dir>/automation_memory.jsonl
```

If `execute_gate` is not exactly `true`, stop. Do not call Gmail, acquire the investment lock, analyze securities, spawn reviewers, or send mail. A manual dry run may pass an explicit aware `--now`; production runs must use the real clock.

## Stage 1

1. Run `PROJECT_ROOT/scripts/evostockctl.py --data-dir <data-dir> status`. Require `status=ACTIVE`; allow `READY_FOR_TASKS` only for an explicitly confirmed dry run.
2. Read [references/run-contract.md](references/run-contract.md) completely and then read the mode-specific root prompts it names.
3. Read `broker_email_profile.json`, call Gmail `get_profile("me")`, and require an exact match before any search, local mutation, recommendation, or send.
4. Acquire the mode-specific `automation_lock.py` lock. If busy or the deterministic scheduled slot already has `EMAIL_SENT`, record a skip and stop.
5. Follow the mode order in the run contract. Facts, decisions, outcomes, candidate experience, and approved rules must remain separate.
6. When a direction-bearing review is due, provide the same fact pack to five actual independent read-only reviewers: holdings facts, company/SEC facts, fundamentals/thesis, valuation/expectations, and risk/counterargument. If the current surface cannot create real independent reviewers, fail closed before sending a direction-bearing email.
7. For production delivery, append `DECISION_CREATED`, then `EMAIL_SEND_INTENT`, then send once, then append `EMAIL_SENT` with the Gmail message ID. Recover uncertain sends by searching the exact Sent marker; never generate a new ID to resend.
8. Release the lock on every Stage 1 exit. Record the run result and unresolved gates in automation memory without storing unverified investment opinions there.

Separate facts, inference, and conditional research actions. Preserve missing data as `待确认`. Never place an order.
