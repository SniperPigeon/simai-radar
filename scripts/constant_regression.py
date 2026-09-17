#!/usr/bin/env python3
"""Train or predict constants without installing mairadar."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mairadar.regression.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
