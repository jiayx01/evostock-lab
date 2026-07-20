# Gmail Broker Bootstrap Contract

## Identity

1. Require the user to confirm the intended Gmail address.
2. Call the connected Gmail provider's profile operation with `me`.
3. Normalize case only and require exact equality. On mismatch, do not search either mailbox.
4. Record the provider name, observed account, and verification time in `deployment.json`; never record tokens.

## Broker Template Discovery

1. Search `in:anywhere` with the user-confirmed broker brand. Traverse every result page.
2. Sample candidate messages and show the user only the minimum fields needed to confirm sender, subject pattern, execution status vocabulary, timezone, and whether quantities are incremental or cumulative.
3. Keep unknown senders, subjects, statuses, corporate actions, or ambiguous quantities quarantined. Do not infer positions from order acknowledgements, cancellations, or subject lines.
4. Write the confirmed profile to `<data-dir>/broker_email_profile.json`. Require non-empty confirmed sender, subject, execution-term, and timezone fields.

## Full-History Ledger

1. Traverse all pages needed for the confirmed history scope. Incomplete pagination fails closed.
2. Normalize each execution into `examples/broker_sync_batch.example.json`. Preserve Gmail message ID, thread ID, received time, sender, subject, content hash, parser version, execution ID or stable message-line proxy, side, ticker, quantity, price, fees, currency, trade time, and status.
3. Apply only verified `FILLED` or `PARTIALLY_FILLED` increments. Keep cancellations and rejects as non-position events.
4. Submit message metadata, index status, events, quarantine, and sync waterline in one batch through `commit_broker_sync_batch.py`. Never edit the derived CSV files independently.
5. If the available mail cannot reconstruct the opening position, permit `VERIFIED_POSITION_ANCHOR` only after full mailbox coverage and with local evidence message IDs. Do not use a typed holding as the production anchor.
6. Show the derived holdings, oldest covered message, event count, and quarantine to the user. Require confirmation before setting `profile_status=CONFIRMED` and `bootstrap_completed_at`.

## Source Priority

Use this public-plugin priority:

1. Verified Gmail broker executions and verified broker anchor.
2. Deterministically derived current holdings.
3. Explicit manual correction only as a separate analysis overlay marked `待确认`.

An overlay never rewrites the broker ledger. A later verified position event makes a sparse overlay stale and requires review.
