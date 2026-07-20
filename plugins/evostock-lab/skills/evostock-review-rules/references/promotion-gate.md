# Rule Promotion Gate

## Evidence Checklist

- Count independent episodes, not repeated daily messages from the same thesis and facts.
- Require at least 20 independent episodes and more than one market regime.
- Use only facts, delivery events, broker events, and completed price bars visible at the explicit `as_of`.
- Use chronological training and holdout windows; never randomize time.
- Include transaction costs, missing-data coverage, worst case, maximum drawdown, downside volatility, and tail loss.
- Compare actual behavior, unchanged-holdings baseline, candidate-rule counterfactual, and current approved rule.
- Keep stopped, rejected, paused, and removed candidates to avoid survivorship bias.
- Treat absent post-delivery reference prices as `PENDING_DATA`, not as zero return.

## Decision States

- `INSUFFICIENT_EVIDENCE`: sample, regime, delivery, or data coverage is below the gate.
- `REJECT`: holdout or risk evidence is worse than the current rule.
- `KEEP_CANDIDATE`: evidence is promising but unstable, incomplete, or contradicted.
- `ELIGIBLE_FOR_USER_APPROVAL`: every quantitative gate passes and counterevidence is disclosed.

## Promotion Record

After explicit approval, append a rule version containing stable rule ID, prior version, approved text, applicable boundary, sample and holdout windows, costs, metrics, evidence hashes, validation command, approval time, and rollback instructions. Never delete the prior rule.

User-specific rules belong in private data. A reusable plugin change belongs in a reviewed source diff and a new plugin version. Do not modify an installed cache as the persistence mechanism.
