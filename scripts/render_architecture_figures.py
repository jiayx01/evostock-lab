#!/usr/bin/env python3
"""Render the repository's Draw.io academic figures from canonical YAML specs."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIAGRAMS = ROOT / "diagrams"
ASSETS = ROOT / "assets"
WORK = ROOT / ".drawio-tmp"
STEMS = ("evostock-learning-loop", "evostock-memory-architecture")
PALETTE_SOURCE = DIAGRAMS / "palettes" / "evostock-academic.json"
PALETTE_TARGET = Path.home() / ".drawio-skill" / "palettes" / PALETTE_SOURCE.name


def resolve_cli() -> Path:
    skill_dir = Path(
        os.environ.get("DRAWIO_SKILL_DIR", Path.home() / ".codex" / "skills" / "drawio")
    ).expanduser()
    cli = skill_dir / "scripts" / "cli.js"
    if not cli.is_file():
        raise SystemExit(
            "Draw.io skill not found. Install it with: "
            "npx skills add bahayonghang/drawio-skills"
        )
    return cli


def run(*args: str) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def sync_palette() -> None:
    PALETTE_TARGET.parent.mkdir(parents=True, exist_ok=True)
    if not PALETTE_TARGET.exists() or PALETTE_TARGET.read_bytes() != PALETTE_SOURCE.read_bytes():
        shutil.copy2(PALETTE_SOURCE, PALETTE_TARGET)


def render(stem: str, cli: Path, node: str) -> None:
    spec = DIAGRAMS / f"{stem}.yaml"
    sidecars = WORK / stem
    sidecars.mkdir(parents=True, exist_ok=True)

    run(
        node,
        str(cli),
        str(spec),
        str(ASSETS / f"{stem}.drawio"),
        "--validate",
        "--write-sidecars",
        "--sidecar-dir",
        str(sidecars),
        "--strict-warnings",
    )
    for suffix in ("svg", "png"):
        run(
            node,
            str(cli),
            str(spec),
            str(ASSETS / f"{stem}.{suffix}"),
            "--validate",
            "--use-desktop",
            "--strict-warnings",
        )


def main() -> None:
    node = shutil.which("node")
    if node is None:
        raise SystemExit("Node.js is required to render Draw.io figures")

    cli = resolve_cli()
    ASSETS.mkdir(exist_ok=True)
    sync_palette()
    for stem in STEMS:
        render(stem, cli, node)
    print("rendered Draw.io academic figures")


if __name__ == "__main__":
    main()
