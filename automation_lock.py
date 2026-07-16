#!/usr/bin/env python3
"""Acquire or release an atomic filesystem lock for long-running automations."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from evostock_paths import data_path


TOKEN_RE = re.compile(r"^[A-Za-z0-9._-]{3,120}$")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def lock_path(root: Path, name: str) -> Path:
    if not TOKEN_RE.fullmatch(name):
        raise ValueError("invalid lock name")
    return root / f"{name}.lock"


def read_owner(path: Path) -> dict:
    try:
        return json.loads((path / "owner.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def lock_age_minutes(path: Path, owner: dict) -> float:
    raw = str(owner.get("started_at") or "").replace("Z", "+00:00")
    try:
        started = datetime.fromisoformat(raw)
        if started.tzinfo is None:
            raise ValueError
        return max(0.0, (now_utc() - started.astimezone(timezone.utc)).total_seconds() / 60.0)
    except ValueError:
        return max(0.0, (now_utc().timestamp() - path.stat().st_mtime) / 60.0)


def acquire(root: Path, name: str, run_id: str, stale_minutes: int) -> int:
    if not TOKEN_RE.fullmatch(run_id):
        raise ValueError("invalid run_id")
    root.mkdir(parents=True, exist_ok=True)
    path = lock_path(root, name)
    for _ in range(2):
        try:
            path.mkdir()
            owner = {"name": name, "run_id": run_id, "started_at": now_utc().isoformat()}
            (path / "owner.json").write_text(
                json.dumps(owner, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(json.dumps({"status": "ACQUIRED", **owner}, ensure_ascii=False))
            return 0
        except FileExistsError:
            owner = read_owner(path)
            age = lock_age_minutes(path, owner)
            if age <= stale_minutes:
                print(
                    json.dumps(
                        {"status": "BUSY", "age_minutes": round(age, 1), "owner": owner},
                        ensure_ascii=False,
                    )
                )
                return 3
            stale_path = root / f"{name}.stale.{int(now_utc().timestamp())}"
            try:
                os.replace(path, stale_path)
                shutil.rmtree(stale_path)
            except FileNotFoundError:
                continue
    print(json.dumps({"status": "BUSY_RACE"}, ensure_ascii=False))
    return 3


def release(root: Path, name: str, run_id: str) -> int:
    path = lock_path(root, name)
    if not path.exists():
        print(json.dumps({"status": "ALREADY_RELEASED"}, ensure_ascii=False))
        return 0
    owner = read_owner(path)
    if owner.get("run_id") != run_id:
        print(json.dumps({"status": "OWNER_MISMATCH", "owner": owner}, ensure_ascii=False))
        return 4
    shutil.rmtree(path)
    print(json.dumps({"status": "RELEASED", "run_id": run_id}, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["acquire", "release"])
    parser.add_argument("--name", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--root", default=data_path(".runtime"))
    parser.add_argument("--stale-minutes", type=int, default=75)
    args = parser.parse_args()
    if args.stale_minutes < 5:
        print("stale-minutes must be at least 5", file=sys.stderr)
        return 2
    try:
        if args.action == "acquire":
            return acquire(Path(args.root), args.name, args.run_id, args.stale_minutes)
        return release(Path(args.root), args.name, args.run_id)
    except (OSError, ValueError) as exc:
        print(f"automation lock failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
