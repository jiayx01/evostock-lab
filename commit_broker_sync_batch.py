#!/usr/bin/env python3
"""Atomically commit one verified Gmail broker-sync batch as an immutable generation."""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from evostock_paths import data_path
from rebuild_holdings_from_broker_events import (
    HOLDING_COLUMNS,
    ReconciliationError,
    ensure_anchor_evidence,
    ensure_no_unresolved_quarantine,
    load_anchor,
    load_events,
    load_profile,
    parse_time as reconciliation_time,
    reconcile,
    resolve_committed_path,
    sha256_path,
    verify_commit_manifest,
)


ID_RE = re.compile(r"^[A-Za-z0-9._:-]{6,200}$")
MESSAGE_INDEX_COLUMNS = [
    "index_event_id",
    "batch_id",
    "source_account",
    "source_message_id",
    "source_thread_id",
    "message_received_at",
    "sender",
    "subject",
    "content_hash",
    "parser_version",
    "lifecycle_type",
    "normalized_event_count",
    "processing_status",
    "processed_at",
    "notes",
]
FINAL_MESSAGE_STATUSES = {"COMMITTED", "IGNORED_NON_POSITION", "QUARANTINED"}


class BrokerSyncError(ValueError):
    pass


@dataclass(frozen=True)
class BrokerPaths:
    profile: Path
    events: Path
    quarantine: Path
    message_index: Path
    sync_state: Path
    anchor: Path
    holdings: Path
    audit: Path
    manifest: Path
    commits_dir: Path
    current_pointer: Path
    lock: Path


GENERATION_NAMES = {
    "broker_events": "broker_events.csv",
    "broker_event_quarantine": "broker_event_quarantine.csv",
    "broker_message_index": "broker_message_index.csv",
    "broker_sync_state": "broker_sync_state.json",
    "holdings": "holdings_current.csv",
    "audit": "reconciliation_audit.json",
    "manifest": "holdings_commit_manifest.json",
}


def parse_aware(value: Any, field: str) -> datetime:
    raw = str(value or "").strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise BrokerSyncError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise BrokerSyncError(f"{field} must include a timezone")
    return parsed


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def batch_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_bytes_synced(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def csv_bytes(fieldnames: list[str], rows: list[dict[str, str]]) -> bytes:
    with tempfile.TemporaryFile("w+", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)
        handle.seek(0)
        return handle.read().encode("utf-8")


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise BrokerSyncError(f"CSV has no header: {path}")
        return list(reader.fieldnames), list(reader)


def upgrade_index_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        {column: str(row.get(column) or "") for column in MESSAGE_INDEX_COLUMNS}
        for row in rows
    ]


def logical_artifacts(paths: BrokerPaths) -> dict[str, Path]:
    return {
        "broker_events": paths.events,
        "broker_event_quarantine": paths.quarantine,
        "broker_message_index": paths.message_index,
        "broker_sync_state": paths.sync_state,
        "holdings": paths.holdings,
        "audit": paths.audit,
        "manifest": paths.manifest,
    }


def expected_link_target(logical_path: Path, paths: BrokerPaths, name: str) -> str:
    target = paths.current_pointer / GENERATION_NAMES[name]
    return os.path.relpath(target, logical_path.parent)


def install_symlink(target: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.link-{uuid.uuid4().hex}"
    os.symlink(target, temporary)
    try:
        os.replace(temporary, destination)
        fsync_directory(destination.parent)
    finally:
        temporary.unlink(missing_ok=True)


def bootstrap_atomic_layout(paths: BrokerPaths) -> None:
    artifacts = logical_artifacts(paths)
    for path in artifacts.values():
        if not path.exists():
            raise BrokerSyncError(f"cannot bootstrap missing broker artifact: {path}")

    if paths.current_pointer.exists() and not paths.current_pointer.is_symlink():
        raise BrokerSyncError(".broker_current exists but is not a symlink")

    if not paths.current_pointer.exists():
        bootstrap_id = (
            "bootstrap-"
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            + "-"
            + uuid.uuid4().hex[:10]
        )
        generation = paths.commits_dir / bootstrap_id
        generation.mkdir(parents=True, exist_ok=False)
        for name, logical_path in artifacts.items():
            write_bytes_synced(generation / GENERATION_NAMES[name], logical_path.read_bytes())
        fsync_directory(generation)
        paths.commits_dir.mkdir(parents=True, exist_ok=True)
        pointer_target = os.path.relpath(generation, paths.current_pointer.parent)
        install_symlink(pointer_target, paths.current_pointer)

    active_generation = paths.current_pointer.resolve()
    for name, logical_path in artifacts.items():
        expected_target = expected_link_target(logical_path, paths, name)
        active_file = active_generation / GENERATION_NAMES[name]
        if not active_file.is_file():
            raise BrokerSyncError(f"atomic bootstrap generation is missing {name}")
        if logical_path.is_symlink():
            if os.readlink(logical_path) != expected_target:
                raise BrokerSyncError(f"unexpected broker artifact symlink: {logical_path}")
            continue
        if logical_path.read_bytes() != active_file.read_bytes():
            raise BrokerSyncError(f"partial atomic migration has divergent content: {logical_path}")
        install_symlink(expected_target, logical_path)


@contextmanager
def broker_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield


def normalize_batch(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise BrokerSyncError("batch must be a schema_version=1 object")
    allowed = {
        "schema_version",
        "run_id",
        "batch_id",
        "target_account",
        "expected_parent_transaction_id",
        "scan",
        "messages",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise BrokerSyncError(f"batch has unknown fields: {', '.join(unknown)}")
    for field in ("run_id", "batch_id", "expected_parent_transaction_id"):
        if not ID_RE.fullmatch(str(value.get(field) or "")):
            raise BrokerSyncError(f"invalid {field}")
    target_account = str(value.get("target_account") or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", target_account):
        raise BrokerSyncError("target_account must be a valid email address")
    scan = value.get("scan")
    if not isinstance(scan, dict):
        raise BrokerSyncError("scan must be an object")
    required_scan = {
        "started_at",
        "completed_at",
        "window_start",
        "pagination_complete",
        "pages_fetched",
        "terminal_next_page_token",
        "history_id_before",
        "history_id_after",
    }
    if set(scan) != required_scan:
        missing = sorted(required_scan - set(scan))
        extra = sorted(set(scan) - required_scan)
        raise BrokerSyncError(f"scan fields differ; missing={missing}, extra={extra}")
    started = parse_aware(scan["started_at"], "scan.started_at")
    completed = parse_aware(scan["completed_at"], "scan.completed_at")
    window_start = parse_aware(scan["window_start"], "scan.window_start")
    if not window_start <= started <= completed:
        raise BrokerSyncError("scan timestamps are out of order")
    if not isinstance(scan["pagination_complete"], bool):
        raise BrokerSyncError("scan.pagination_complete must be boolean")
    if not isinstance(scan["pages_fetched"], int) or scan["pages_fetched"] < 1:
        raise BrokerSyncError("scan.pages_fetched must be a positive integer")
    if not str(scan.get("history_id_before") or "").strip():
        raise BrokerSyncError("scan.history_id_before is required")
    if not str(scan.get("history_id_after") or "").strip():
        raise BrokerSyncError("scan.history_id_after is required")
    messages = value.get("messages")
    if not isinstance(messages, list):
        raise BrokerSyncError("messages must be a list")
    return value


def stable_index_id(batch_id: str, message_id: str, status: str) -> str:
    raw = f"{batch_id}|{message_id}|{status}"
    return "idx-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def normalize_messages(
    batch: dict[str, Any], event_columns: list[str], quarantine_columns: list[str]
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]], list[str]]:
    events: list[dict[str, str]] = []
    quarantine_rows: list[dict[str, str]] = []
    index_rows: list[dict[str, str]] = []
    unresolved_ids: list[str] = []
    seen_messages: set[str] = set()
    scan = batch["scan"]
    scan_started = parse_aware(scan["started_at"], "scan.started_at")
    scan_completed = parse_aware(scan["completed_at"], "scan.completed_at")
    batch_id = str(batch["batch_id"])
    account = str(batch["target_account"]).lower()

    for position, message in enumerate(batch["messages"], start=1):
        if not isinstance(message, dict):
            raise BrokerSyncError(f"message {position} must be an object")
        allowed_message = {
            "source_message_id",
            "source_thread_id",
            "message_received_at",
            "sender",
            "subject",
            "content_hash",
            "parser_version",
            "lifecycle_type",
            "processing_status",
            "discovered_at",
            "processed_at",
            "events",
            "quarantine",
            "notes",
        }
        unknown = sorted(set(message) - allowed_message)
        if unknown:
            raise BrokerSyncError(f"message {position} has unknown fields: {', '.join(unknown)}")
        message_id = str(message.get("source_message_id") or "").strip()
        if not message_id or message_id in seen_messages:
            raise BrokerSyncError(f"message {position} has missing or duplicate source_message_id")
        seen_messages.add(message_id)
        received_at = parse_aware(
            message.get("message_received_at"), f"message {position}.message_received_at"
        )
        discovered_at = parse_aware(
            message.get("discovered_at") or scan_started.isoformat(),
            f"message {position}.discovered_at",
        )
        processed_at = parse_aware(
            message.get("processed_at") or scan_completed.isoformat(),
            f"message {position}.processed_at",
        )
        if received_at > scan_completed or discovered_at > processed_at:
            raise BrokerSyncError(f"message {position} timestamps are out of order")
        status = str(message.get("processing_status") or "").strip().upper()
        if status not in FINAL_MESSAGE_STATUSES:
            raise BrokerSyncError(f"message {position} has unsupported processing_status")
        sender = str(message.get("sender") or "").strip()
        subject = str(message.get("subject") or "").strip()
        content_hash = str(message.get("content_hash") or "").strip()
        parser_version = str(message.get("parser_version") or "").strip()
        lifecycle = str(message.get("lifecycle_type") or "").strip().upper()
        if not sender or not subject or not content_hash or not parser_version or not lifecycle:
            raise BrokerSyncError(f"message {position} is missing verified metadata")
        raw_events = message.get("events")
        if not isinstance(raw_events, list):
            raise BrokerSyncError(f"message {position}.events must be a list")
        if status == "COMMITTED" and not raw_events:
            raise BrokerSyncError(f"message {position} COMMITTED without normalized events")
        if status != "COMMITTED" and raw_events:
            raise BrokerSyncError(f"message {position} has events but is not COMMITTED")

        for event_number, raw_event in enumerate(raw_events, start=1):
            if not isinstance(raw_event, dict):
                raise BrokerSyncError(f"message {position} event {event_number} must be an object")
            unknown_event = sorted(set(raw_event) - set(event_columns))
            if unknown_event:
                raise BrokerSyncError(
                    f"message {position} event {event_number} has unknown fields: "
                    + ", ".join(unknown_event)
                )
            row = {column: str(raw_event.get(column) or "") for column in event_columns}
            derived = {
                "source_account": account,
                "source_message_id": message_id,
                "source_thread_id": str(message.get("source_thread_id") or ""),
                "message_received_at": received_at.isoformat(),
                "sender": sender,
                "subject": subject,
                "content_hash": content_hash,
                "parser_version": parser_version,
                "parsed_at": processed_at.isoformat(),
            }
            for field, expected in derived.items():
                if row.get(field) and row[field] != expected:
                    raise BrokerSyncError(
                        f"message {position} event {event_number} conflicts on {field}"
                    )
                row[field] = expected
            if str(row.get("affects_position") or "").strip().lower() in {
                "true",
                "1",
                "yes",
            }:
                trade_at = parse_aware(
                    row.get("trade_time"),
                    f"message {position} event {event_number}.trade_time",
                )
                if trade_at > scan_completed:
                    raise BrokerSyncError(
                        f"message {position} event {event_number} is after scan completion"
                    )
            events.append(row)

        quarantine = message.get("quarantine")
        if quarantine is not None:
            if not isinstance(quarantine, dict):
                raise BrokerSyncError(f"message {position}.quarantine must be an object")
            unknown_quarantine = sorted(set(quarantine) - set(quarantine_columns))
            if unknown_quarantine:
                raise BrokerSyncError(
                    f"message {position}.quarantine has unknown fields: "
                    + ", ".join(unknown_quarantine)
                )
            qrow = {column: str(quarantine.get(column) or "") for column in quarantine_columns}
            derived_q = {
                "source_account": account,
                "source_message_id": message_id,
                "source_thread_id": str(message.get("source_thread_id") or ""),
                "message_received_at": received_at.isoformat(),
                "sender": sender,
                "subject": subject,
                "parsed_at": processed_at.isoformat(),
            }
            for field, expected in derived_q.items():
                if field not in qrow:
                    continue
                if qrow[field] and qrow[field] != expected:
                    raise BrokerSyncError(f"message {position} quarantine conflicts on {field}")
                qrow[field] = expected
            resolution = str(qrow.get("resolution_status") or "").strip().upper()
            if not str(qrow.get("reason") or "").strip() or not resolution:
                raise BrokerSyncError(f"message {position} quarantine lacks reason or status")
            if resolution not in {"RESOLVED", "FALSE_POSITIVE"}:
                unresolved_ids.append(message_id)
            quarantine_rows.append(qrow)
        elif status == "QUARANTINED":
            raise BrokerSyncError(f"message {position} QUARANTINED without quarantine record")

        common_index = {
            "batch_id": batch_id,
            "source_account": account,
            "source_message_id": message_id,
            "source_thread_id": str(message.get("source_thread_id") or ""),
            "message_received_at": received_at.isoformat(),
            "sender": sender,
            "subject": subject,
            "content_hash": content_hash,
            "parser_version": parser_version,
            "lifecycle_type": lifecycle,
            "normalized_event_count": str(len(raw_events)),
            "notes": str(message.get("notes") or ""),
        }
        discovered = {column: "" for column in MESSAGE_INDEX_COLUMNS}
        discovered.update(common_index)
        discovered.update(
            {
                "index_event_id": stable_index_id(batch_id, message_id, "DISCOVERED"),
                "processing_status": "DISCOVERED",
                "processed_at": discovered_at.isoformat(),
            }
        )
        final = {column: "" for column in MESSAGE_INDEX_COLUMNS}
        final.update(common_index)
        final.update(
            {
                "index_event_id": stable_index_id(batch_id, message_id, status),
                "processing_status": status,
                "processed_at": processed_at.isoformat(),
            }
        )
        index_rows.extend([discovered, final])
    return events, quarantine_rows, index_rows, unresolved_ids


def merge_rows(
    existing: list[dict[str, str]], incoming: list[dict[str, str]], columns: list[str]
) -> list[dict[str, str]]:
    normalized = [{column: str(row.get(column) or "") for column in columns} for row in existing]
    seen = {tuple(row[column] for column in columns) for row in normalized}
    identities: dict[str, tuple[str, ...]] = {}
    if "index_event_id" in columns:
        for row in normalized:
            identity = row.get("index_event_id") or ""
            if identity:
                identities[identity] = tuple(row[column] for column in columns)
    for row in incoming:
        candidate = {column: str(row.get(column) or "") for column in columns}
        signature = tuple(candidate[column] for column in columns)
        identity = candidate.get("index_event_id") or ""
        if identity and identity in identities and identities[identity] != signature:
            raise BrokerSyncError(f"index_event_id conflicts: {identity}")
        if signature in seen:
            continue
        normalized.append(candidate)
        seen.add(signature)
        if identity:
            identities[identity] = signature
    return normalized


def latest_quarantine_unresolved(rows: list[dict[str, str]]) -> list[str]:
    latest: dict[str, dict[str, str]] = {}
    for row in rows:
        message_id = str(row.get("source_message_id") or "").strip()
        latest[message_id or f"row-{len(latest)}"] = row
    return sorted(
        message_id
        for message_id, row in latest.items()
        if str(row.get("resolution_status") or "").strip().upper()
        not in {"RESOLVED", "FALSE_POSITIVE"}
    )


def validate_message_event_linkage(
    event_rows: list[dict[str, str]], index_rows: list[dict[str, str]]
) -> None:
    latest: dict[str, dict[str, str]] = {}
    for row in index_rows:
        latest[str(row.get("source_message_id") or "").strip()] = row
    counts: dict[str, int] = {}
    for row in event_rows:
        message_id = str(row.get("source_message_id") or "").strip()
        counts[message_id] = counts.get(message_id, 0) + 1
    for message_id, count in counts.items():
        state = latest.get(message_id)
        if not state or str(state.get("processing_status") or "").upper() not in {
            "COMMITTED",
            "ANCHOR_EVIDENCE",
        }:
            raise BrokerSyncError(f"broker event has no committed message index state: {message_id}")
        expected = str(state.get("normalized_event_count") or "").strip()
        if expected and int(expected) != count:
            raise BrokerSyncError(f"message index event count differs for {message_id}")


def validate_history_waterline(before: str, after: str) -> None:
    if before.isdigit() and after.isdigit() and int(after) < int(before):
        raise BrokerSyncError("history_id_after cannot move backwards")


def build_manifest(
    paths: BrokerPaths,
    generation: Path,
    transaction_id: str,
    commit_status: str,
    target_account: str,
    parent_transaction_id: str,
    parent_manifest_sha256: str,
    batch_id: str,
    normalized_batch_sha256: str,
    scan: dict[str, Any] | None,
) -> dict[str, Any]:
    artifact = lambda name: {
        "path": str((generation / GENERATION_NAMES[name]).resolve()),
        "sha256": sha256_path(generation / GENERATION_NAMES[name]),
    }
    return {
        "manifest_version": 3,
        "atomic_layout": "immutable_generation_pointer_v1",
        "transaction_id": transaction_id,
        "commit_status": commit_status,
        "committed_at": datetime.now(timezone.utc).isoformat(),
        "target_account": target_account,
        "generation_path": str(generation.resolve()),
        "parent_transaction_id": parent_transaction_id,
        "parent_manifest_sha256": parent_manifest_sha256,
        "batch_id": batch_id,
        "batch_sha256": normalized_batch_sha256,
        "scan": scan,
        "logical_paths": {
            name: str(path.absolute())
            for name, path in logical_artifacts(paths).items()
            if name != "manifest"
        },
        "holdings_path": artifact("holdings")["path"],
        "holdings_sha256": artifact("holdings")["sha256"],
        "audit_path": artifact("audit")["path"],
        "audit_sha256": artifact("audit")["sha256"],
        "transaction_files": {
            name: artifact(name)
            for name in (
                "broker_events",
                "broker_event_quarantine",
                "broker_message_index",
                "broker_sync_state",
            )
        },
        "source_inputs": {
            "profile": {"path": str(paths.profile.resolve()), "sha256": sha256_path(paths.profile)},
            "anchor": {"path": str(paths.anchor.resolve()), "sha256": sha256_path(paths.anchor)},
        },
    }


def publish_generation(
    paths: BrokerPaths,
    transaction_id: str,
    artifacts: dict[str, bytes],
    manifest_metadata: dict[str, Any],
) -> dict[str, Any]:
    paths.commits_dir.mkdir(parents=True, exist_ok=True)
    temporary = paths.commits_dir / f".tmp-{transaction_id}-{uuid.uuid4().hex[:8]}"
    final = paths.commits_dir / transaction_id
    if final.exists():
        raise BrokerSyncError("transaction generation already exists")
    temporary.mkdir()
    try:
        for name, content in artifacts.items():
            write_bytes_synced(temporary / GENERATION_NAMES[name], content)
        manifest = build_manifest(paths, temporary, transaction_id, **manifest_metadata)
        # Paths in the active manifest must reference the immutable final directory.
        encoded = canonical_json(manifest).replace(str(temporary.resolve()), str(final.resolve()))
        manifest = json.loads(encoded)
        write_bytes_synced(temporary / GENERATION_NAMES["manifest"], json_bytes(manifest))
        fsync_directory(temporary)
        os.replace(temporary, final)
        fsync_directory(paths.commits_dir)
        pointer_target = os.path.relpath(final, paths.current_pointer.parent)
        install_symlink(pointer_target, paths.current_pointer)
        return manifest
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
        raise


def active_paths(manifest: dict[str, Any], paths: BrokerPaths) -> dict[str, Path]:
    return {
        "broker_events": resolve_committed_path(manifest, "broker_events", paths.events),
        "broker_event_quarantine": resolve_committed_path(
            manifest, "broker_event_quarantine", paths.quarantine
        ),
        "broker_message_index": resolve_committed_path(
            manifest, "broker_message_index", paths.message_index
        ),
        "broker_sync_state": resolve_committed_path(
            manifest, "broker_sync_state", paths.sync_state
        ),
        "holdings": resolve_committed_path(manifest, "holdings", paths.holdings),
        "audit": resolve_committed_path(manifest, "audit", paths.audit),
    }


def find_batch_in_active_chain(
    current: dict[str, Any], commits_dir: Path, wanted_batch_id: str, wanted_hash: str
) -> dict[str, Any] | None:
    manifests: list[tuple[dict[str, Any], bytes]] = []
    if commits_dir.exists():
        for candidate in commits_dir.glob(f"*/{GENERATION_NAMES['manifest']}"):
            try:
                content = candidate.read_bytes()
                value = json.loads(content)
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(value, dict) and value.get("transaction_id"):
                manifests.append((value, content))

    cursor = current
    visited: set[str] = set()
    while cursor:
        transaction_id = str(cursor.get("transaction_id") or "")
        if not transaction_id or transaction_id in visited:
            break
        visited.add(transaction_id)
        if cursor.get("batch_id") == wanted_batch_id:
            if cursor.get("batch_sha256") != wanted_hash:
                raise BrokerSyncError("batch_id already committed with different content")
            return cursor
        parent_id = str(cursor.get("parent_transaction_id") or "")
        parent_hash = str(cursor.get("parent_manifest_sha256") or "")
        if not parent_id or not parent_hash:
            break
        matches = [
            value
            for value, content in manifests
            if value.get("transaction_id") == parent_id
            and hashlib.sha256(content).hexdigest() == parent_hash
        ]
        if len(matches) > 1:
            raise BrokerSyncError("active generation chain has an ambiguous parent")
        cursor = matches[0] if matches else {}
    return None


def migrate_existing(paths: BrokerPaths) -> dict[str, Any]:
    original_manifest_bytes = paths.manifest.read_bytes()
    current = verify_commit_manifest(paths.manifest, paths.holdings, allow_blocked=True)
    bootstrap_atomic_layout(paths)
    current = verify_commit_manifest(paths.manifest, paths.holdings, allow_blocked=True)
    if current.get("manifest_version") == 3:
        return {"status": "ALREADY_V3", "transaction_id": current["transaction_id"]}
    active = active_paths(current, paths)
    _, existing_index = read_csv(active["broker_message_index"])
    sync_state = json.loads(active["broker_sync_state"].read_text(encoding="utf-8"))
    audit = json.loads(active["audit"].read_text(encoding="utf-8"))
    transaction_id = (
        "broker-migration-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        + "-"
        + uuid.uuid4().hex[:10]
    )
    audit.update(
        {
            "status": "SUCCESS",
            "transaction_id": transaction_id,
            "atomic_layout_migration": True,
            "migrated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    sync_state.update(
        {
            "state_version": 2,
            "last_transaction_id": transaction_id,
            "last_commit_status": "SUCCESS",
        }
    )
    artifacts = {
        "broker_events": active["broker_events"].read_bytes(),
        "broker_event_quarantine": active["broker_event_quarantine"].read_bytes(),
        "broker_message_index": csv_bytes(
            MESSAGE_INDEX_COLUMNS, upgrade_index_rows(existing_index)
        ),
        "broker_sync_state": json_bytes(sync_state),
        "holdings": active["holdings"].read_bytes(),
        "audit": json_bytes(audit),
    }
    manifest = publish_generation(
        paths,
        transaction_id,
        artifacts,
        {
            "commit_status": "SUCCESS",
            "target_account": str(sync_state.get("target_account") or "").lower(),
            "parent_transaction_id": str(current.get("transaction_id") or ""),
            "parent_manifest_sha256": hashlib.sha256(original_manifest_bytes).hexdigest(),
            "batch_id": f"migration-{transaction_id}",
            "normalized_batch_sha256": hashlib.sha256(b"atomic-layout-migration-v3").hexdigest(),
            "scan": None,
        },
    )
    verify_commit_manifest(paths.manifest, paths.holdings)
    return {"status": "MIGRATED", "transaction_id": manifest["transaction_id"]}


def commit_batch(paths: BrokerPaths, raw_batch: Any) -> dict[str, Any]:
    batch = normalize_batch(raw_batch)
    normalized_hash = batch_hash(batch)
    parent_manifest_bytes = paths.manifest.read_bytes()
    current = verify_commit_manifest(paths.manifest, paths.holdings, allow_blocked=True)
    bootstrap_atomic_layout(paths)
    current = verify_commit_manifest(paths.manifest, paths.holdings, allow_blocked=True)
    prior_batch = find_batch_in_active_chain(
        current, paths.commits_dir, str(batch["batch_id"]), normalized_hash
    )
    if prior_batch is not None:
        return {
            "status": "DUPLICATE_NOOP",
            "transaction_id": prior_batch["transaction_id"],
            "commit_status": prior_batch["commit_status"],
        }
    if batch["expected_parent_transaction_id"] != current.get("transaction_id"):
        raise BrokerSyncError("STALE_PARENT: expected_parent_transaction_id differs")

    active = active_paths(current, paths)
    event_columns, existing_events = read_csv(active["broker_events"])
    quarantine_columns, existing_quarantine = read_csv(active["broker_event_quarantine"])
    _, raw_existing_index = read_csv(active["broker_message_index"])
    existing_index = upgrade_index_rows(raw_existing_index)
    sync_state = json.loads(active["broker_sync_state"].read_text(encoding="utf-8"))
    if str(sync_state.get("target_account") or "").lower() != batch["target_account"].lower():
        raise BrokerSyncError("sync state belongs to another account")
    history_before = str(batch["scan"]["history_id_before"])
    history_after = str(batch["scan"]["history_id_after"])
    if history_before != str(sync_state.get("last_verified_history_id") or ""):
        raise BrokerSyncError("scan.history_id_before differs from current waterline")
    validate_history_waterline(history_before, history_after)
    last_scan = parse_aware(sync_state.get("last_successful_scan_at"), "last_successful_scan_at")
    completed_at = parse_aware(batch["scan"]["completed_at"], "scan.completed_at")
    if completed_at < last_scan:
        raise BrokerSyncError("scan.completed_at cannot move backwards")

    incoming_events, incoming_quarantine, incoming_index, batch_unresolved = normalize_messages(
        batch, event_columns, quarantine_columns
    )
    complete = bool(batch["scan"]["pagination_complete"])
    terminal_token = batch["scan"].get("terminal_next_page_token")
    blocked_reasons: list[str] = []
    if not complete:
        blocked_reasons.append("pagination_incomplete")
    if terminal_token not in {None, ""}:
        blocked_reasons.append("terminal_next_page_token_not_empty")
    if batch_unresolved:
        blocked_reasons.append("unresolved_quarantine_in_batch")

    discovered_only = [
        row for row in incoming_index if row["processing_status"] == "DISCOVERED"
    ]
    candidate_quarantine = merge_rows(
        existing_quarantine, incoming_quarantine, quarantine_columns
    )
    unresolved_all = latest_quarantine_unresolved(candidate_quarantine)
    if unresolved_all:
        blocked_reasons.append("unresolved_quarantine")
    commit_status = "BLOCKED" if blocked_reasons else "SUCCESS"

    if commit_status == "SUCCESS":
        merged_events = merge_rows(existing_events, incoming_events, event_columns)
        merged_index = merge_rows(existing_index, incoming_index, MESSAGE_INDEX_COLUMNS)
        merged_quarantine = candidate_quarantine
        validate_message_event_linkage(merged_events, merged_index)
    else:
        merged_events = existing_events
        merged_index = merge_rows(existing_index, discovered_only, MESSAGE_INDEX_COLUMNS)
        merged_quarantine = candidate_quarantine

    transaction_id = (
        "broker-sync-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        + "-"
        + hashlib.sha256(str(batch["batch_id"]).encode("utf-8")).hexdigest()[:10]
        + "-"
        + uuid.uuid4().hex[:8]
    )
    new_sync_state = dict(sync_state)
    new_sync_state.update(
        {
            "state_version": 2,
            "last_transaction_id": transaction_id,
            "last_commit_status": commit_status,
            "last_batch_id": batch["batch_id"],
            "last_batch_sha256": normalized_hash,
            "last_attempt_at": datetime.now(timezone.utc).isoformat(),
        }
    )

    if commit_status == "SUCCESS":
        new_sync_state["last_successful_scan_at"] = completed_at.isoformat()
        new_sync_state["last_verified_history_id"] = history_after
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            staged_events = temporary / "events.csv"
            staged_quarantine = temporary / "quarantine.csv"
            staged_index = temporary / "message_index.csv"
            write_bytes_synced(staged_events, csv_bytes(event_columns, merged_events))
            write_bytes_synced(
                staged_quarantine, csv_bytes(quarantine_columns, merged_quarantine)
            )
            write_bytes_synced(
                staged_index, csv_bytes(MESSAGE_INDEX_COLUMNS, merged_index)
            )
            profile = load_profile(paths.profile)
            ensure_no_unresolved_quarantine(staged_quarantine)
            ensure_anchor_evidence(staged_index, profile)
            initial_positions, anchor_at = load_anchor(paths.anchor, profile)
            parsed_events, _ = load_events(staged_events)
            holdings_rows, audit = reconcile(
                profile,
                parsed_events,
                reconciliation_time(completed_at.isoformat(), "scan_completed_at", 0),
                initial_positions,
                anchor_at,
            )
        audit.update(
            {
                "status": "SUCCESS",
                "transaction_id": transaction_id,
                "run_id": batch["run_id"],
                "batch_id": batch["batch_id"],
                "batch_sha256": normalized_hash,
                "parent_transaction_id": current.get("transaction_id"),
                "scan": batch["scan"],
                "waterline_before": history_before,
                "waterline_after": history_after,
                "atomic_layout": "immutable_generation_pointer_v1",
            }
        )
        holdings_content = csv_bytes(HOLDING_COLUMNS, holdings_rows)
    else:
        audit = {
            "status": "BLOCKED",
            "transaction_id": transaction_id,
            "run_id": batch["run_id"],
            "batch_id": batch["batch_id"],
            "batch_sha256": normalized_hash,
            "parent_transaction_id": current.get("transaction_id"),
            "scan": batch["scan"],
            "blocked_reasons": sorted(set(blocked_reasons)),
            "unresolved_message_ids": unresolved_all,
            "waterline_before": history_before,
            "waterline_after": history_before,
            "atomic_layout": "immutable_generation_pointer_v1",
        }
        holdings_content = active["holdings"].read_bytes()

    artifacts = {
        "broker_events": csv_bytes(event_columns, merged_events),
        "broker_event_quarantine": csv_bytes(quarantine_columns, merged_quarantine),
        "broker_message_index": csv_bytes(MESSAGE_INDEX_COLUMNS, merged_index),
        "broker_sync_state": json_bytes(new_sync_state),
        "holdings": holdings_content,
        "audit": json_bytes(audit),
    }
    manifest = publish_generation(
        paths,
        transaction_id,
        artifacts,
        {
            "commit_status": commit_status,
            "target_account": batch["target_account"].lower(),
            "parent_transaction_id": str(current.get("transaction_id") or ""),
            "parent_manifest_sha256": hashlib.sha256(parent_manifest_bytes).hexdigest(),
            "batch_id": str(batch["batch_id"]),
            "normalized_batch_sha256": normalized_hash,
            "scan": batch["scan"],
        },
    )
    verify_commit_manifest(
        paths.manifest, paths.holdings, allow_blocked=commit_status == "BLOCKED"
    )
    return {
        "status": "COMMITTED",
        "commit_status": commit_status,
        "transaction_id": manifest["transaction_id"],
        "waterline_before": history_before,
        "waterline_after": history_after if commit_status == "SUCCESS" else history_before,
        "blocked_reasons": audit.get("blocked_reasons", []),
    }


def parse_paths(args: argparse.Namespace) -> BrokerPaths:
    return BrokerPaths(
        profile=Path(args.profile),
        events=Path(args.events),
        quarantine=Path(args.quarantine),
        message_index=Path(args.message_index),
        sync_state=Path(args.sync_state),
        anchor=Path(args.anchor),
        holdings=Path(args.holdings),
        audit=Path(args.audit),
        manifest=Path(args.manifest),
        commits_dir=Path(args.commits_dir),
        current_pointer=Path(args.current_pointer),
        lock=Path(args.lock),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", help="schema_version=1 broker batch JSON")
    parser.add_argument("--migrate-existing", action="store_true")
    parser.add_argument("--profile", default=data_path("broker_email_profile.json"))
    parser.add_argument("--events", default=data_path("broker_events.csv"))
    parser.add_argument("--quarantine", default=data_path("broker_event_quarantine.csv"))
    parser.add_argument("--message-index", default=data_path("broker_message_index.csv"))
    parser.add_argument("--sync-state", default=data_path("broker_sync_state.json"))
    parser.add_argument("--anchor", default=data_path("holdings_anchor.csv"))
    parser.add_argument("--holdings", default=data_path("holdings_current.csv"))
    parser.add_argument(
        "--audit", default=data_path("reports", "latest_holdings_reconciliation.json")
    )
    parser.add_argument("--manifest", default=data_path("holdings_commit_manifest.json"))
    parser.add_argument("--commits-dir", default=data_path(".broker_commits"))
    parser.add_argument("--current-pointer", default=data_path(".broker_current"))
    parser.add_argument("--lock", default=data_path(".runtime", "broker-sync.lock"))
    args = parser.parse_args()
    if args.migrate_existing == bool(args.input):
        parser.error("choose exactly one of --input or --migrate-existing")
    paths = parse_paths(args)
    try:
        with broker_lock(paths.lock):
            if args.migrate_existing:
                result = migrate_existing(paths)
            else:
                with Path(args.input).open(encoding="utf-8") as handle:
                    result = commit_batch(paths, json.load(handle))
    except (OSError, json.JSONDecodeError, ReconciliationError, BrokerSyncError) as exc:
        print(f"broker sync commit failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
