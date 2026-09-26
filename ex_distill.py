#!/usr/bin/env python3
"""Backward-compatible launcher for the modular xpskill package."""

from __future__ import annotations

import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from src.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
