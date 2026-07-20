---
name: evostock-setup
description: Deploy or repair Gmail-backed EvoStock portfolio automation for Claude Code or Codex. Use when the user asks to install EvoStock, authenticate Gmail, bootstrap broker holdings, create recurring portfolio-review tasks, select or switch the active executor, or redeploy a broken automation. Never bypass OAuth or treat unverified manual holdings as the production source.
---

# EvoStock Setup

Deploy one auditable local automation. Require one interactive Gmail authorization and one broker-template confirmation; automate the remaining setup.

## Resolve Paths

Resolve `PLUGIN_ROOT` as the parent of this Skill's `skills/` directory. Default `PROJECT_ROOT` to `~/.evostock-lab/app` and private state to `~/.evostock-lab/data`; never store runtime state in the installed plugin cache. Keep OAuth tokens outside EvoStock files.

## Hard Gates

- Select exactly one executor, `codex` or `claude`. Do not deploy both schedulers for the same data directory.
- Require the platform Gmail connector. Read [references/platform-scheduling.md](references/platform-scheduling.md) for platform setup.
- Call Gmail `get_profile("me")` before any mailbox search. Require an exact, case-normalized match with the user-confirmed target account.
- Use verified Gmail execution events as the production holding source. Manual holdings may only create a clearly labeled correction overlay; never bootstrap or silently merge the broker ledger from them.
- Never create an order, request trading credentials, or promise returns.

## Deploy

1. Ask only for the target Gmail account, IANA timezone, executor, and preset (`daily`, `intraday`, or `full`). Explain the persistent paths and get confirmation before writing outside the current project.
   If a deployment already exists and any of these settings must change, inspect and disable every recorded task on the old executor first. Only then reinitialize with `--replace` after explicit confirmation; never switch executors by layering a second schedule over the first.
2. Read the plugin version from its manifest. If this is a source checkout containing the engine two directories above `PLUGIN_ROOT`, use that checkout for development. Otherwise clone `https://github.com/jiayx01/evostock-lab.git` at the matching `v<plugin-version>` tag into `PROJECT_ROOT`. Refuse an unexpected remote or dirty replacement; never update an existing engine checkout silently.
3. Create `~/.evostock-lab/venv`, install `PROJECT_ROOT/requirements.txt`, and run all subsequent project scripts with that interpreter.
4. Run `PROJECT_ROOT/bootstrap_local_data.py --data-dir <data-dir>`. It must not overwrite existing files.
5. Initialize the deployment state:

   ```bash
   <runtime-python> <PROJECT_ROOT>/scripts/evostockctl.py \
     --data-dir <data-dir> init \
     --target-account <account> \
     --executor <codex-or-claude> \
     --timezone <iana-timezone> \
     --preset <preset> \
     --runtime-python <runtime-python> \
     --project-root <PROJECT_ROOT>
   ```

6. Ensure the Gmail connector is authorized, then call `get_profile("me")`. On an exact match, record the evidence with `evostockctl.py verify-gmail`. On any mismatch, stop before search, ledger mutation, analysis, task creation, or send.
7. Read [references/gmail-bootstrap.md](references/gmail-bootstrap.md) completely. Discover and confirm the broker template, traverse every page, build the first atomic broker generation, and show the derived holdings plus quarantine to the user. Do not activate while any holding-affecting event is unresolved.
8. Set `broker_email_profile.json.profile_status=CONFIRMED` only after the user confirms the sampled template and full-history coverage. Run `evostockctl.py verify-broker --profile <profile-path>`.
9. Run `evostockctl.py plan`. Present the task names, local wakeups, permissions, and active executor. Obtain explicit confirmation before creating scheduled tasks.
10. Create platform-native local tasks from the returned plan. Use the exact invocation emitted in each prompt: `$evostock-run` for Codex and `/evostock-lab:evostock-run` for Claude Code. Preserve its `mode`, data directory, runtime Python, project root, and Stage 0 requirement. Record each returned task ID with `evostockctl.py record-task`.
11. Run one `dry_run=true` review. It may read verified Gmail and generate a local report, but must not send email or append a production decision.
12. After the user approves the dry-run output, enable the tasks and run `evostockctl.py activate`. Report the account, broker profile hash, executor, task IDs, next wakeups, and data directory. Never print tokens or message bodies.

If setup stops, leave the deployment non-active and report the first failed gate plus the exact recovery action.
