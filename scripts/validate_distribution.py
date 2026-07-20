#!/usr/bin/env python3
"""Validate the cross-agent plugin distribution without vendor CLIs."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.evostockctl import PLUGIN_VERSION


PLUGIN_ROOT = ROOT / "plugins" / "evostock-lab"
EXPECTED_SKILLS = {
    "evostock-review-rules",
    "evostock-run",
    "evostock-setup",
    "evostock-status",
}
FORBIDDEN_TEXT = (
    "[TODO" + ":",
    "/Users/" + "jiayexiang",
    "jyxiang01" + "@gmail.com",
)
FORBIDDEN_PATTERNS = {
    "OAuth access token": re.compile(r"\bya29\.[A-Za-z0-9._-]{20,}"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}


def read_json(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{path.relative_to(ROOT)}: {exc}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{path.relative_to(ROOT)} must contain a JSON object")
        return {}
    return value


def skill_frontmatter(path: Path, errors: list[str]) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        errors.append(f"{path.relative_to(ROOT)} has invalid frontmatter boundaries")
        return {}
    block = text[4 : text.index("\n---\n", 4)]
    values: dict[str, str] = {}
    for line in block.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip()
    return values


def validate_distribution() -> list[str]:
    errors: list[str] = []
    codex_manifest = read_json(PLUGIN_ROOT / ".codex-plugin" / "plugin.json", errors)
    claude_manifest = read_json(PLUGIN_ROOT / ".claude-plugin" / "plugin.json", errors)
    codex_market = read_json(ROOT / ".agents" / "plugins" / "marketplace.json", errors)
    claude_market = read_json(ROOT / ".claude-plugin" / "marketplace.json", errors)

    for label, manifest in (
        ("Codex manifest", codex_manifest),
        ("Claude manifest", claude_manifest),
    ):
        if manifest.get("name") != "evostock-lab":
            errors.append(f"{label} name must be evostock-lab")
        if manifest.get("version") != PLUGIN_VERSION:
            errors.append(f"{label} version must match {PLUGIN_VERSION}")
        if manifest.get("skills") != "./skills/":
            errors.append(f"{label} skills path must be ./skills/")

    expected_source = "./plugins/evostock-lab"
    codex_plugins = codex_market.get("plugins", [])
    if len(codex_plugins) != 1:
        errors.append("Codex marketplace must expose exactly one plugin")
    elif codex_plugins[0].get("source", {}).get("path") != expected_source:
        errors.append("Codex marketplace source path is incorrect")
    claude_plugins = claude_market.get("plugins", [])
    if len(claude_plugins) != 1:
        errors.append("Claude marketplace must expose exactly one plugin")
    elif claude_plugins[0].get("source") != expected_source:
        errors.append("Claude marketplace source path is incorrect")

    skills_root = PLUGIN_ROOT / "skills"
    actual_skills = {path.name for path in skills_root.iterdir() if path.is_dir()}
    if actual_skills != EXPECTED_SKILLS:
        errors.append(
            f"skill set mismatch: expected {sorted(EXPECTED_SKILLS)}, got {sorted(actual_skills)}"
        )
    for name in sorted(EXPECTED_SKILLS & actual_skills):
        skill_file = skills_root / name / "SKILL.md"
        if not skill_file.is_file():
            errors.append(f"missing skill file: {skill_file.relative_to(ROOT)}")
            continue
        frontmatter = skill_frontmatter(skill_file, errors)
        if frontmatter.get("name") != name:
            errors.append(f"{skill_file.relative_to(ROOT)} name must match its folder")
        if not frontmatter.get("description"):
            errors.append(f"{skill_file.relative_to(ROOT)} needs a description")
        agent_yaml = skills_root / name / "agents" / "openai.yaml"
        if not agent_yaml.is_file():
            errors.append(f"missing Codex skill metadata: {agent_yaml.relative_to(ROOT)}")

    text_suffixes = {".csv", ".json", ".md", ".py", ".txt", ".yaml", ".yml"}
    ignored_parts = {".git", ".venv", "__pycache__", "data"}
    paths = [
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and path.suffix.lower() in text_suffixes
        and not ignored_parts.intersection(path.relative_to(ROOT).parts)
    ]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for marker in FORBIDDEN_TEXT:
            if marker in text:
                errors.append(f"{path.relative_to(ROOT)} contains forbidden text {marker!r}")
        for label, pattern in FORBIDDEN_PATTERNS.items():
            if pattern.search(text):
                errors.append(f"{path.relative_to(ROOT)} contains a forbidden {label}")

    return errors


def main() -> int:
    errors = validate_distribution()
    if errors:
        print("Distribution validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Distribution validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
