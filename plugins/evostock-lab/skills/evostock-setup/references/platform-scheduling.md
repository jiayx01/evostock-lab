# Platform Scheduling

## Shared Rules

- Deploy one active executor per data directory. Record every platform task ID.
- Use the local persistent data directory and runtime Python returned by setup.
- Create tasks only after Gmail and broker verification and explicit user confirmation.
- Test every task with `Run now` in dry-run mode before activation.
- The schedule may include both US daylight and standard-time local wakeups. This is intentional; `automation_gate.py` rejects the inactive offset and repeated scheduled slots.
- Scheduled tasks must invoke the platform-specific `evostock-run` entrypoint explicitly. Do not paste the entire investment workflow into every task prompt.

## Codex

Use ChatGPT desktop Scheduled Tasks in Codex or Work mode. Local project tasks require the computer on and the desktop app running. Codex CLI and the IDE extension can validate the skill but do not provide the Scheduled management interface.

Create or update tasks from a Codex chat when the scheduling capability is available. Use the task ID returned by the platform in `evostockctl.py record-task`. If scheduling is unavailable, stop with the plan; do not mark the deployment active.

Use `$evostock-run` in every Codex scheduled-task prompt.

## Claude Code

Use Claude Code Desktop Local Scheduled Tasks. They require the computer on and the app running, but do not require an open terminal session. Do not use `/loop` for production: it is session-scoped and expires.

Use `/evostock-lab:evostock-run` in every Claude Code scheduled-task prompt.

Claude cloud Routines use a fresh clone and cannot use the local private ledger without a separately designed encrypted state service. Treat cloud deployment as unsupported in this local-first release.

## Gmail

- In Codex, install and authorize the official Gmail plugin/connector separately if it is not already available.
- In Claude Code, connect Gmail in claude.ai Settings > Connectors; the authenticated connector then appears in Claude Code sessions using the same account.
- Connector authorization is a separate security decision from installing EvoStock. Never claim plugin installation grants mailbox access.
