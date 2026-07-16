"""Centralized paths for tracked configuration and private runtime state."""

from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("EVOSTOCK_DATA_DIR", PROJECT_ROOT / "data")).expanduser()
CONFIG_DIR = PROJECT_ROOT / "config"


def data_path(*parts: str) -> str:
    return str(DATA_DIR.joinpath(*parts))


def config_path(*parts: str) -> str:
    return str(CONFIG_DIR.joinpath(*parts))
