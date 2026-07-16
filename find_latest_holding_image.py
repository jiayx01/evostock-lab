#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from evostock_paths import data_path


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".heic", ".heif", ".tif", ".tiff"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Find the newest holding screenshot in the project directory.")
    parser.add_argument("--dir", default=data_path("screenshots"))
    parser.add_argument("--recursive", action="store_true")
    args = parser.parse_args()

    root = Path(args.dir).expanduser().resolve()
    iterator = root.rglob("*") if args.recursive else root.iterdir()
    images = [p for p in iterator if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS]

    if not images:
        print(json.dumps({"found": False, "root": str(root)}, ensure_ascii=False, indent=2))
        return

    latest = max(images, key=lambda p: p.stat().st_mtime)
    mtime = datetime.fromtimestamp(latest.stat().st_mtime)
    today = datetime.now().date()
    result = {
        "found": True,
        "path": str(latest),
        "filename": latest.name,
        "modified_at": mtime.strftime("%Y-%m-%d %H:%M:%S"),
        "is_today": mtime.date() == today,
        "root": str(root),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
