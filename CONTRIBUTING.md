# Contributing

Contributions should preserve the project's evidence-first boundaries:

- No automatic order execution or guaranteed-return language.
- No future data in historical decision or outcome calculations.
- Missing values remain missing; they are not converted to zero or neutral signals.
- State-changing ledgers stay append-only and idempotent.
- New decision rules require tests, a stated sample window, and an explicit failure boundary.
- Fixtures must use synthetic accounts, message IDs, holdings, and prices.

Before opening a pull request, run:

```bash
python -m unittest discover -s tests -v
```
