# Automated Run Contract

## Required Order

1. Pass Stage 0 and obtain a deterministic `scheduled_slot`.
2. Verify deployment is active.
3. Verify Gmail profile identity and confirmed broker profile.
4. Acquire the mode lock and check decision/email idempotency.
5. Settle outcomes that are mature at the explicit `as_of` before recalling experience.
6. Reconcile Gmail executions into one atomic broker generation.
7. Build the market, holding, company, valuation, and risk fact pack.
8. Run independent review and main-agent arbitration.
9. Append the decision and idempotent delivery chain.
10. Collect the first complete post-delivery reference bar when due.
11. Release the lock and append operational run state.

## Mode Inputs

- `intraday`: read `intraday_portfolio_automation_prompt.md` and `daily_portfolio_automation_prompt.md`; rescan the confirmed recent Gmail overlap and produce a direction-bearing review.
- `daily-review`: read `midnight_portfolio_automation_prompt.md`, `daily_portfolio_automation_prompt.md`, and `portfolio_memory_strategy.md`; settle mature outcomes before the consolidated review.
- `post-close`: read `post_close_portfolio_automation_prompt.md` and `portfolio_memory_strategy.md`; collect official completed bars and settle outcomes without inventing a new trade signal.
- `weekly-review`: use the post-close contract, group mature episodes by regime/action/factor, and update candidate experience only.
- `monthly-review`: use the post-close contract and create versioned rule or Skill proposals. Promotion still requires explicit user approval.

## Gmail Reconciliation

Call `get_profile("me")` before read and again before send. Traverse all pages in the configured overlap. Normalize each message into the batch schema and call `commit_broker_sync_batch.py` once. A connector error, unknown template, incomplete page traversal, conflicting duplicate, unresolved position event, or manifest mismatch stops new direction-bearing advice.

The production source order is verified Gmail broker events or anchor, then derived holdings. Manual corrections remain a separate overlay and never mutate the broker ledger.

## Evidence and Review

Use completed market bars and record `as_of`, source time, collection time, timezone, and market phase. Cover SPY, QQQ, IWM, SMH or SOXX, IGV, VIX, RSP/SPY breadth, HYG/IEF credit appetite, and each holding. Verify SEC, earnings, valuation expectations, security structure, and material news where available; otherwise write `待确认`.

Direction-bearing production runs require five actual read-only reviews using the same facts. Record failures and disagreement; do not simulate missing reviewers in the main response.

## Decisions and Delivery

Generate IDs deterministically from the scheduled slot. Keep facts immutable. Append `DECISION_CREATED`, `EMAIL_SEND_INTENT`, and exactly one terminal delivery event. Search Gmail Sent for the exact marker before recovering an uncertain send. A successful delivery must record its Gmail message ID.

Email one concise consolidated result: overall action, per-holding action, conditions, prohibited action, strongest counterevidence, experience/outcome context, new-opening opportunity, and uncertainties. End with the no-guarantee and no-auto-trading disclaimer.

## Learning Boundary

Daily PnL may create candidate experience but cannot change a live rule. Weekly review evaluates stability. Monthly review may propose a change only after at least 20 independent episodes, multiple regimes, chronological holdout, transaction costs, improved risk-adjusted results, and no worse maximum drawdown.
