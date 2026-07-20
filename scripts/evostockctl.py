#!/usr/bin/env python3
"""Manage the auditable deployment state for the EvoStock automation plugin."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


PLUGIN_VERSION = "0.2.0"
NEW_YORK = ZoneInfo("America/New_York")
ACCOUNT_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
TASK_POLICIES = {
    "intraday": {
        "mode": "intraday",
        "slots_et": ["09:30", "10:30", "11:30", "12:30"],
    },
    "daily-review": {"mode": "daily-review", "slots_et": ["12:30"]},
    "post-close": {"mode": "post-close", "slots_et": ["16:30"]},
    "weekly-review": {"mode": "weekly-review", "slots_et": ["16:45"]},
    "monthly-review": {"mode": "monthly-review", "slots_et": ["17:00"]},
}
PRESET_TASKS = {
    "daily": ("daily-review", "post-close"),
    "intraday": ("intraday", "post-close"),
    "full": ("intraday", "post-close", "weekly-review", "monthly-review"),
}
SKILL_INVOCATIONS = {
    "codex": "$evostock-run",
    "claude": "/evostock-lab:evostock-run",
}


class ControlError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_account(value: str) -> str:
    account = value.strip().lower()
    if not ACCOUNT_RE.fullmatch(account):
        raise ControlError("target account must be a valid email address")
    return account


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ControlError(f"missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ControlError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ControlError(f"expected a JSON object in {path}")
    return value


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def state_path(data_dir: Path) -> Path:
    return data_dir.expanduser() / "deployment.json"


def load_state(data_dir: Path) -> dict[str, Any]:
    return load_json_object(state_path(data_dir))


def save_state(data_dir: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = utc_now()
    atomic_write_json(state_path(data_dir), state)


def required_tasks(state: dict[str, Any]) -> tuple[str, ...]:
    preset = state.get("preset")
    if preset not in PRESET_TASKS:
        raise ControlError(f"unsupported preset in deployment state: {preset}")
    return PRESET_TASKS[preset]


def deployment_issues(state: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    gmail = state.get("gmail", {})
    if gmail.get("status") != "VERIFIED":
        issues.append("GMAIL_NOT_VERIFIED")
    else:
        if gmail.get("provider") != state.get("executor"):
            issues.append("GMAIL_PROVIDER_MISMATCH")
        observed = str(gmail.get("observed_account", "")).strip().lower()
        target = str(state.get("target_account", "")).strip().lower()
        if not observed or observed != target:
            issues.append("GMAIL_ACCOUNT_MISMATCH")

    broker = state.get("broker", {})
    if broker.get("status") != "VERIFIED":
        issues.append("BROKER_NOT_VERIFIED")
    else:
        profile_path = broker.get("profile_path")
        expected_hash = broker.get("profile_sha256")
        if not profile_path or not expected_hash:
            issues.append("BROKER_PROFILE_EVIDENCE_MISSING")
        else:
            try:
                actual_hash = sha256_file(Path(str(profile_path)).expanduser())
            except OSError:
                issues.append("BROKER_PROFILE_UNREADABLE")
            else:
                if actual_hash != expected_hash:
                    issues.append("BROKER_PROFILE_HASH_MISMATCH")

    tasks = state.get("tasks", {})
    for task_kind in required_tasks(state):
        task = tasks.get(task_kind)
        if not isinstance(task, dict) or not task.get("platform_task_id"):
            issues.append(f"TASK_NOT_RECORDED:{task_kind}")
        elif not task.get("enabled"):
            issues.append(f"TASK_NOT_ENABLED:{task_kind}")
    return issues


def _clock(value: str) -> time:
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise ControlError(f"invalid ET slot in policy: {value}") from exc
    return parsed


def local_wakeups(timezone_name: str, slots_et: list[str]) -> list[dict[str, Any]]:
    try:
        local_zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ControlError(f"unknown IANA timezone: {timezone_name}") from exc

    current_year = datetime.now(timezone.utc).year
    reference_dates = (date(current_year, 1, 15), date(current_year, 7, 15))
    wakeups: set[tuple[str, int]] = set()
    for reference_date in reference_dates:
        for slot_value in slots_et:
            source = datetime.combine(reference_date, _clock(slot_value), tzinfo=NEW_YORK)
            local = source.astimezone(local_zone)
            day_offset = (local.date() - source.date()).days
            wakeups.add((local.strftime("%H:%M"), day_offset))
    return [
        {"local_time": local_time, "et_day_offset": day_offset}
        for local_time, day_offset in sorted(wakeups, key=lambda item: (item[1], item[0]))
    ]


def schedule_plan(state: dict[str, Any], data_dir: Path) -> dict[str, Any]:
    timezone_name = str(state["timezone"])
    executor = state.get("executor")
    try:
        skill_invocation = SKILL_INVOCATIONS[executor]
    except KeyError as exc:
        raise ControlError(f"unsupported executor in deployment state: {executor}") from exc
    tasks = []
    for task_kind in required_tasks(state):
        policy = TASK_POLICIES[task_kind]
        mode = policy["mode"]
        tasks.append(
            {
                "kind": task_kind,
                "mode": mode,
                "schedule_source_timezone": "America/New_York",
                "slots_et": policy["slots_et"],
                "local_wakeups": local_wakeups(timezone_name, policy["slots_et"]),
                "window_minutes": 15,
                "prompt": (
                    f"Use {skill_invocation} with mode={mode}. Set EVOSTOCK_DATA_DIR to "
                    f"{data_dir.expanduser()}, use runtime Python {state['runtime_python']}, and run "
                    f"the engine at {state['project_root']}. Run Stage 0 before loading Gmail or "
                    "investment context."
                ),
            }
        )
    return {
        "executor": executor,
        "timezone": timezone_name,
        "preset": state["preset"],
        "tasks": tasks,
        "note": (
            "Create only one executor's tasks. Duplicate DST wakeups are intentional; "
            "automation_gate.py rejects the inactive offset and duplicate scheduled slots."
        ),
    }


def cmd_init(args: argparse.Namespace) -> dict[str, Any]:
    data_dir = args.data_dir.expanduser()
    path = state_path(data_dir)
    account = normalize_account(args.target_account)
    try:
        ZoneInfo(args.timezone)
    except ZoneInfoNotFoundError as exc:
        raise ControlError(f"unknown IANA timezone: {args.timezone}") from exc

    if path.exists() and not args.replace:
        current = load_state(data_dir)
        if current.get("target_account") != account:
            raise ControlError(
                "deployment already targets a different Gmail account; use --replace only after explicit confirmation"
            )
        requested = {
            "executor": args.executor,
            "timezone": args.timezone,
            "preset": args.preset,
            "runtime_python": str(Path(args.runtime_python).expanduser().resolve()),
            "project_root": str(Path(args.project_root).expanduser().resolve()),
        }
        changed = [key for key, value in requested.items() if current.get(key) != value]
        if changed:
            raise ControlError(
                "deployment already exists with different settings for "
                + ", ".join(changed)
                + "; disable the recorded platform tasks, then use --replace after explicit confirmation"
            )
        return {"result": "UNCHANGED", "deployment": current, "path": str(path)}

    now = utc_now()
    state = {
        "schema_version": 1,
        "plugin_version": PLUGIN_VERSION,
        "status": "DRAFT",
        "executor": args.executor,
        "target_account": account,
        "timezone": args.timezone,
        "preset": args.preset,
        "runtime_python": str(Path(args.runtime_python).expanduser().resolve()),
        "project_root": str(Path(args.project_root).expanduser().resolve()),
        "created_at": now,
        "updated_at": now,
        "gmail": {"status": "PENDING_AUTHORIZATION"},
        "broker": {"status": "PENDING_VERIFICATION"},
        "tasks": {},
    }
    atomic_write_json(path, state)
    return {"result": "CREATED", "deployment": state, "path": str(path)}


def cmd_verify_gmail(args: argparse.Namespace) -> dict[str, Any]:
    state = load_state(args.data_dir)
    if args.provider != state.get("executor"):
        raise ControlError(
            f"Gmail provider {args.provider} does not match active executor {state.get('executor')}; "
            "state was not changed"
        )
    expected = normalize_account(state["target_account"])
    observed = normalize_account(args.observed_account)
    if observed != expected:
        raise ControlError(
            f"Gmail identity mismatch: expected {expected}, observed {observed}; state was not changed"
        )
    state["gmail"] = {
        "status": "VERIFIED",
        "observed_account": observed,
        "provider": args.provider,
        "verified_at": utc_now(),
    }
    if state["status"] == "DRAFT":
        state["status"] = "GMAIL_VERIFIED"
    save_state(args.data_dir, state)
    return {"result": "VERIFIED", "gmail": state["gmail"]}


def validate_broker_profile(profile: dict[str, Any], expected_account: str) -> None:
    if normalize_account(str(profile.get("target_account", ""))) != expected_account:
        raise ControlError("broker profile target_account does not match deployment")
    if profile.get("profile_status") != "CONFIRMED":
        raise ControlError("broker profile_status must be CONFIRMED")
    required_lists = (
        "confirmed_senders",
        "confirmed_subject_patterns",
        "confirmed_execution_terms",
    )
    for field in required_lists:
        value = profile.get(field)
        if not isinstance(value, list) or not any(str(item).strip() for item in value):
            raise ControlError(f"broker profile field {field} must contain confirmed values")
    if profile.get("confirmed_timezone") in (None, "", "PENDING"):
        raise ControlError("broker profile confirmed_timezone is required")
    if not profile.get("bootstrap_completed_at"):
        raise ControlError("full-history bootstrap must complete before broker verification")


def cmd_verify_broker(args: argparse.Namespace) -> dict[str, Any]:
    state = load_state(args.data_dir)
    if state.get("gmail", {}).get("status") != "VERIFIED":
        raise ControlError("verify Gmail before verifying a broker profile")
    profile_path = args.profile.expanduser().resolve()
    profile = load_json_object(profile_path)
    expected = normalize_account(state["target_account"])
    validate_broker_profile(profile, expected)
    state["broker"] = {
        "status": "VERIFIED",
        "broker": str(profile.get("broker", "unknown")),
        "profile_path": str(profile_path),
        "profile_sha256": sha256_file(profile_path),
        "verified_at": utc_now(),
    }
    state["status"] = "READY_FOR_TASKS"
    save_state(args.data_dir, state)
    return {"result": "VERIFIED", "broker": state["broker"]}


def cmd_plan(args: argparse.Namespace) -> dict[str, Any]:
    state = load_state(args.data_dir)
    return schedule_plan(state, args.data_dir)


def cmd_record_task(args: argparse.Namespace) -> dict[str, Any]:
    state = load_state(args.data_dir)
    if args.kind not in required_tasks(state):
        raise ControlError(f"task kind {args.kind} is not required by preset {state['preset']}")
    if args.platform != state["executor"]:
        raise ControlError(
            f"task platform {args.platform} does not match active executor {state['executor']}"
        )
    state.setdefault("tasks", {})[args.kind] = {
        "platform": args.platform,
        "platform_task_id": args.task_id,
        "schedule": args.schedule,
        "enabled": True,
        "recorded_at": utc_now(),
    }
    save_state(args.data_dir, state)
    return {"result": "RECORDED", "kind": args.kind, "task": state["tasks"][args.kind]}


def cmd_activate(args: argparse.Namespace) -> dict[str, Any]:
    state = load_state(args.data_dir)
    issues = deployment_issues(state)
    if issues:
        raise ControlError("deployment is not ready: " + ", ".join(issues))
    state["status"] = "ACTIVE"
    state["activated_at"] = utc_now()
    save_state(args.data_dir, state)
    return {"result": "ACTIVE", "deployment": state}


def cmd_pause(args: argparse.Namespace) -> dict[str, Any]:
    state = load_state(args.data_dir)
    state["status"] = "PAUSED"
    state["paused_at"] = utc_now()
    save_state(args.data_dir, state)
    return {"result": "PAUSED", "deployment": state}


def cmd_resume(args: argparse.Namespace) -> dict[str, Any]:
    state = load_state(args.data_dir)
    issues = deployment_issues(state)
    if issues:
        raise ControlError("deployment cannot resume: " + ", ".join(issues))
    state["status"] = "ACTIVE"
    state["resumed_at"] = utc_now()
    save_state(args.data_dir, state)
    return {"result": "ACTIVE", "deployment": state}


def cmd_status(args: argparse.Namespace) -> dict[str, Any]:
    path = state_path(args.data_dir)
    if not path.exists():
        return {
            "configured": False,
            "status": "NOT_CONFIGURED",
            "path": str(path),
            "issues": ["DEPLOYMENT_NOT_INITIALIZED"],
        }
    state = load_state(args.data_dir)
    issues = deployment_issues(state)
    return {
        "configured": True,
        "status": state.get("status"),
        "ready_for_activation": not issues,
        "issues": issues,
        "path": str(path),
        "deployment": state,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("EVOSTOCK_DATA_DIR", Path.home() / ".evostock-lab" / "data")),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    initialize = subparsers.add_parser("init")
    initialize.add_argument("--target-account", required=True)
    initialize.add_argument("--executor", choices=("codex", "claude"), required=True)
    initialize.add_argument("--timezone", required=True)
    initialize.add_argument("--preset", choices=sorted(PRESET_TASKS), default="full")
    initialize.add_argument("--runtime-python", default=sys.executable)
    initialize.add_argument(
        "--project-root", default=str(Path(__file__).resolve().parents[1])
    )
    initialize.add_argument("--replace", action="store_true")
    initialize.set_defaults(handler=cmd_init)

    gmail = subparsers.add_parser("verify-gmail")
    gmail.add_argument("--observed-account", required=True)
    gmail.add_argument("--provider", choices=("codex", "claude"), required=True)
    gmail.set_defaults(handler=cmd_verify_gmail)

    broker = subparsers.add_parser("verify-broker")
    broker.add_argument("--profile", type=Path, required=True)
    broker.set_defaults(handler=cmd_verify_broker)

    plan = subparsers.add_parser("plan")
    plan.set_defaults(handler=cmd_plan)

    task = subparsers.add_parser("record-task")
    task.add_argument("--kind", choices=sorted(TASK_POLICIES), required=True)
    task.add_argument("--platform", choices=("codex", "claude"), required=True)
    task.add_argument("--task-id", required=True)
    task.add_argument("--schedule", required=True)
    task.set_defaults(handler=cmd_record_task)

    for name, handler in (
        ("activate", cmd_activate),
        ("pause", cmd_pause),
        ("resume", cmd_resume),
        ("status", cmd_status),
    ):
        command = subparsers.add_parser(name)
        command.set_defaults(handler=handler)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = args.handler(args)
    except (ControlError, OSError) as exc:
        print(
            json.dumps(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
