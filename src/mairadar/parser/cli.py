"""Small batch CLI: input, output, optional difficulty and safe replacement."""

import argparse
from pathlib import Path
import sys

from mairadar import __version__
from mairadar.io import write_bundle
from .core import parse_file


def _difficulty(value: str) -> int:
    try:
        number = int(value)
        if number < 1:
            raise ValueError()
        return number
    except ValueError as exc:
        raise argparse.ArgumentTypeError("difficulty must be a positive inote index") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Parse Simai into one CSV bundle per difficulty chart.")
    parser.add_argument("--input", "-i", type=Path, required=True, help="maidata/majdata or raw Simai file, or directory")
    parser.add_argument("--output", "-o", type=Path, required=True, help="output root; each chart gets a child directory")
    parser.add_argument("--difficulty", "-d", type=_difficulty, nargs="+", help="inote indexes, e.g. 5 6; default: all present")
    parser.add_argument("--overwrite", action="store_true", help="replace matching generated chart bundles")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)
    source = args.input.resolve()
    if not source.exists():
        parser.error(f"input does not exist: {source}")
    if source.is_file():
        root, files = source.parent, [source]
    elif source.is_dir():
        root = source
        output = args.output.resolve()
        if source == output:
            parser.error("output must differ from the input directory")
        files = sorted(p for p in source.rglob("*") if p.is_file()
                       and (p.name.lower() in {"maidata.txt", "majdata.txt"} or p.suffix.lower() == ".simai")
                       and not p.is_relative_to(output))
    else:
        parser.error("input must be a regular file or directory")
    if not files:
        parser.error("no maidata.txt, majdata.txt or .simai files found")
    complete = partial = failures = 0
    for file in files:
        try:
            bundles = parse_file(file, source_name=file.relative_to(root).as_posix(), difficulties=args.difficulty)
        except (OSError, UnicodeError, ValueError) as exc:
            print(f"{file}: {exc}", file=sys.stderr)
            failures += 1
            continue
        for bundle in bundles:
            try:
                target = write_bundle(bundle, args.output, overwrite=args.overwrite)
            except (OSError, ValueError) as exc:
                print(f"{file} [difficulty {bundle.chart.difficulty_index}]: {exc}", file=sys.stderr)
                failures += 1
                continue
            if bundle.complete:
                complete += 1
            else:
                partial += 1
            print(f"{'complete' if bundle.complete else 'partial'} {target} events={len(bundle.events)} diagnostics={len(bundle.diagnostics)}")
            for diagnostic in bundle.diagnostics:
                if diagnostic.severity != "info":
                    print(f"  {file}:{diagnostic.source_line}:{diagnostic.source_column} {diagnostic.code}: {diagnostic.message}", file=sys.stderr)
    print(f"charts_complete={complete} charts_partial={partial} failures={failures}")
    return 1 if partial or failures else 0
