---
name: evostock-review-rules
description: Review EvoStock candidate experience and proposed rule or Skill changes against mature no-lookahead outcomes. Use when the user asks what the system learned, whether a candidate rule should be promoted, how memory is evolving, or to approve, reject, compare, or roll back a production rule. Never promote from one-day PnL or silently rewrite an installed Skill.
---

# EvoStock Rule Review

Keep the five memory layers separate: facts, decisions, outcomes, candidate experience, and approved rules. Automation memory is an operational cursor, not investment knowledge.

## Review

1. Resolve the configured `PROJECT_ROOT` and data directory. Read `portfolio_memory_strategy.md`, `experience/candidate_rules.md`, `experience/approved_rules.md`, decision episodes, and mature outcomes.
2. Read [references/promotion-gate.md](references/promotion-gate.md) completely.
3. Recompute the evidence with an explicit `as_of`. Exclude immature, undelivered, tampered, duplicate-episode, and future-contaminated observations.
4. Compare the candidate against the current approved rule and a no-change baseline. Report independent sample count, market-regime coverage, transaction-cost assumptions, holdout risk-adjusted result, maximum drawdown, worst case, missing coverage, and strongest counterevidence.
5. Return one status: `INSUFFICIENT_EVIDENCE`, `REJECT`, `KEEP_CANDIDATE`, or `ELIGIBLE_FOR_USER_APPROVAL`.
6. Do not change production rules without explicit user approval. Approval must append a versioned rule record with evidence window, hashes, validation command, prior version, and rollback instructions.
7. Store user-specific approved rules in the private rule overlay. For a reusable plugin-Skill change, generate a proposed diff or pull request; never edit the installed plugin cache in place.

Do not claim that temporal proximity proves the user followed a recommendation. Never use a promoted rule to automate order execution.
