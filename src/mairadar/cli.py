"""Unified CLI: parse, analyze, score, and export through independent layers."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

from . import __version__
from .pipeline import MODES, SCORING_MODES, run_pipeline


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
    source.add_argument(
        "--input", "-i", type=Path,
        help="full/parse_only: raw file or root; analysis modes: bundle root",
    )
    source.add_argument("--choose", action="store_true", help="choose the input directory")
    parser.add_argument(
        "--output", "-o", type=Path,
        help="new or empty bundle/report directory; omitted only for analysis",
    )
    parser.add_argument(
        "--format", choices=("csv", "visualizer"), default="csv",
        help="scoring output format (default: csv)",
    )
    parser.add_argument(
        "--difficulty", "-d", type=_difficulty, nargs="+",
        help="chart indexes; distribution modes exclude Utage/7 unless explicitly selected",
    )
    parser.add_argument(
        "--include-utage", action="store_true",
        help="include Utage/7 alongside ordinary charts in distribution modes",
    )
    parser.add_argument(
        "--chart-type", choices=("dx", "sd"),
        help="full/parse_only: explicit chart type override",
    )
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
        if args.mode in SCORING_MODES and transformer is None:
            from .scoring.config import TRANSFORMER
            if TRANSFORMER is not None:
                transformer = TRANSFORMER()
        if args.mode not in SCORING_MODES and args.format != "csv":
            raise ValueError("--format is only available in scoring modes")
        if exporter is None and args.format == "visualizer":
            from .exporters import VisualizerExporter
            exporter = VisualizerExporter()
        batch, report = run_pipeline(
            args.mode, args.input, output=args.output, analyzer=analyzer,
            transformer=transformer, exporter=exporter,
            difficulties=args.difficulty, chart_type=args.chart_type,
            include_utage=args.include_utage,
        )
        if args.mode == "parse_only":
            print(
                f"bundles={batch.exported_bundles} "
                f"failed_or_partial={batch.failed_records} output={batch.output_path}"
            )
        elif report is None:
            for record in batch.records:
                print(json.dumps({
                    "source": record.source_name,
                    "chart": asdict(record.chart) if record.chart is not None else None,
                    "status": record.status,
                    "analysis": asdict(record.analysis) if record.analysis is not None else None,
                    "diagnostics": [asdict(issue) for issue in record.diagnostics],
                }, ensure_ascii=False, allow_nan=False))
        else:
            if hasattr(report, "site_path"):
                artifact = f"visualizer={report.site_path}"
            else:
                artifact = f"csv={report.csv_path}"
            print(f"charts={len(batch.records)} failed_or_partial={report.failed_records} {artifact}")
        for record in batch.records:
            if record.status != "ok":
                if args.mode == "parse_only":
                    if record.bundle_path is not None:
                        detail = f"see {record.bundle_path / 'diagnostics.csv'}"
                    else:
                        detail = "; ".join(
                            f"{issue.code}: {issue.message}" for issue in record.diagnostics
                        )
                    print(f"{record.source_name}: {record.status}; {detail}", file=sys.stderr)
                else:
                    print(
                        f"{record.source_name}: {record.status}; see result diagnostics",
                        file=sys.stderr,
                    )
        return max(batch.exit_code, report.exit_code if report is not None else 0)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
