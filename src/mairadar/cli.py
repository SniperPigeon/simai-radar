"""Unified CLI: full, analysis, and analysis_score compose independent layers."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

from . import __version__
from .pipeline import MODES, run_pipeline


def choose_directory() -> Path | None:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    try:
        selected = filedialog.askdirectory(title="Select the input directory", mustexist=True)
        return Path(selected) if selected else None
    finally:
        root.destroy()


def _difficulty(value: str) -> int:
    try:
        number = int(value)
        if number < 1:
            raise ValueError()
        return number
    except ValueError as exc:
        raise argparse.ArgumentTypeError("difficulty must be a positive inote index") from exc


def main(argv: list[str] | None = None, *, analyzer=None, transformer=None, exporter=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=MODES, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", "-i", type=Path, help="full: raw file/root; other modes: bundle root")
    source.add_argument("--choose", action="store_true", help="choose the input directory")
    parser.add_argument("--output", "-o", type=Path, help="new or empty report directory; scoring modes only")
    parser.add_argument("--difficulty", "-d", type=_difficulty, nargs="+", help="full only: inote indexes")
    parser.add_argument("--chart-type", choices=("dx", "sd"), help="full only: explicit chart type override")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)
    try:
        if args.choose:
            try:
                args.input = choose_directory()
            except Exception as exc:
                print(f"Folder chooser unavailable: {exc}; use --input PATH", file=sys.stderr)
                return 1
            if args.input is None:
                print("Folder selection cancelled", file=sys.stderr)
                return 1
        if args.mode != "analysis" and transformer is None:
            from .scoring.config import TRANSFORMER
            if TRANSFORMER is not None:
                transformer = TRANSFORMER()
        batch, report = run_pipeline(
            args.mode, args.input, output=args.output, analyzer=analyzer,
            transformer=transformer, exporter=exporter,
            difficulties=args.difficulty, chart_type=args.chart_type,
        )
        if report is None:
            for record in batch.records:
                print(json.dumps({
                    "source": record.source_name,
                    "chart": asdict(record.chart) if record.chart is not None else None,
                    "status": record.status,
                    "analysis": asdict(record.analysis) if record.analysis is not None else None,
                    "diagnostics": [asdict(issue) for issue in record.diagnostics],
                }, ensure_ascii=False, allow_nan=False))
        else:
            print(f"charts={len(batch.records)} failed_or_partial={report.failed_records} csv={report.csv_path}")
        for record in batch.records:
            if record.status != "ok":
                print(f"{record.source_name}: {record.status}; see result diagnostics", file=sys.stderr)
        return max(batch.exit_code, report.exit_code if report is not None else 0)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
