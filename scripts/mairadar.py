#!/usr/bin/env python3
"""Run the unified CLI without installing the package."""

from pathlib import Path
import sys

if sys.version_info < (3, 11):
    raise SystemExit("mairadar requires Python 3.11 or newer")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mairadar.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
