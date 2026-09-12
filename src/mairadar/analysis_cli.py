"""Compatibility alias for the unified CLI's analysis mode."""

import sys

from .cli import main as unified_main


def main(argv: list[str] | None = None) -> int:
    return unified_main(["--mode", "analysis", *(sys.argv[1:] if argv is None else argv)])


if __name__ == "__main__":
    raise SystemExit(main())
