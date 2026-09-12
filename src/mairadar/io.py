"""Validated CSV bundle I/O. JSON cells are decoded here, outside analysis."""

from dataclasses import asdict, fields
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from . import __version__
from .model import (Chart, ChartBundle, Diagnostic, Event, MAJDATAPLAY_PIN,
                    MAJSIMAI_PIN, SCHEMA_VERSION)
from .validation import validate_bundle

TABLES = {"charts": "charts.csv", "events": "events.csv", "diagnostics": "diagnostics.csv"}
JSON_FIELDS = {"metadata_json", "slide_path_json", "flags_json", "recovery_json"}
BOOL_FIELDS = {"is_slide_head", "is_break", "is_ex", "is_mine"}
INT_FIELDS = {"event_id", "head_event_id", "difficulty_index", "source_start", "source_end", "source_line", "source_column"}
FLOAT_FIELDS = {"slide_declare_time_s", "start_time_s", "end_time_s", "bpm", "offset_s",
                "chart_end_time_s", "last_event_end_s", "audio_duration_s"}
NULLABLE = {"source_name", "chart_type", "difficulty_index", "difficulty_label", "level_text", "title", "artist", "designer",
            "timing_type", "start_beat", "end_beat", "position", "head_event_id"} | BOOL_FIELDS | FLOAT_FIELDS


def parse_file(path: str | Path, *, difficulties: list[int] | None = None,
               source_name: str | None = None) -> list[ChartBundle]:
    """File adapter. The parser itself only receives decoded text."""
    from .parser import parse_text

    path = Path(path)
    bundles = parse_text(path.read_bytes().decode("utf-8-sig"), difficulties=difficulties)
    for bundle in bundles:
        bundle.chart.source_name = source_name if source_name is not None else path.name
        if not bundle.chart.title:
            bundle.chart.title = path.stem
    return bundles


def bundle_directory_name(chart: Chart) -> str:
    """Human-readable export name, never a song identity or deduplication key."""
    if not chart.title or not chart.title.strip():
        raise ValueError("Export requires a title in chart metadata")
    if chart.difficulty_index is None:
        raise ValueError("Export requires difficulty_index; use --difficulty for raw text")
    if chart.chart_type not in {"dx", "sd"}:
        raise ValueError("Export requires cabinet=DX/SD metadata or --chart-type dx/sd")
    # Only filesystem-unsafe characters change; original metadata stays intact.
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", chart.title).strip().rstrip(".")
    if not title:
        raise ValueError("Title cannot form a directory name")
    return f"{title}-{chart.difficulty_index}-{chart.chart_type}"


def _write_csv(path: Path, model: type, rows: list) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=[f.name for f in fields(model)])
        writer.writeheader()
        for row in rows:
            data = asdict(row)
            for key, value in data.items():
                if value is None:
                    data[key] = ""
                elif key in JSON_FIELDS:
                    data[key] = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                elif isinstance(value, bool):
                    data[key] = int(value)
            writer.writerow(data)


def _read_csv(path: Path, model: type) -> list:
    output = []
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != [f.name for f in fields(model)]:
            raise ValueError(f"Unexpected {path.name} columns")
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed CSV row in {path.name}")
            for key, value in row.items():
                if value == "" and key in (NULLABLE | JSON_FIELDS):
                    row[key] = None
                elif key in JSON_FIELDS:
                    row[key] = json.loads(value)
                elif key in BOOL_FIELDS:
                    if value not in {"0", "1"}:
                        raise ValueError("Boolean cells must be 0, 1 or empty")
                    row[key] = value == "1"
                elif key in INT_FIELDS:
                    row[key] = int(value)
                elif key in FLOAT_FIELDS:
                    row[key] = float(value)
            output.append(model(**row))
    return output


def write_bundle(bundle: ChartBundle, output: str | Path, *, overwrite: bool = False) -> Path:
    """Write one chart directory, staging all files before replacing old output.

    Replacement is allowed only for a same-format generated bundle, with no extra
    user files. On rename failure the previous directory is restored.
    """
    validate_bundle(bundle)
    name = bundle_directory_name(bundle.chart)
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    target = root / name
    if target.is_symlink():
        raise ValueError(f"Refusing symlink output: {target}")
    if target.exists():
        if not overwrite:
            raise FileExistsError(f"Output exists: {target}; use --overwrite to replace this bundle")
        old = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        if old.get("schema_version") != SCHEMA_VERSION or old.get("tables") != TABLES:
            raise ValueError("Refusing to replace an unrelated output directory")
        if {p.name for p in target.iterdir()} != set(TABLES.values()) | {"manifest.json"}:
            raise ValueError("Output contains extra files; refusing replacement")
    staging = Path(tempfile.mkdtemp(prefix=f".{name}-", dir=root))
    backup = None
    try:
        rows = {"charts": (Chart, [bundle.chart]), "events": (Event, bundle.events),
                "diagnostics": (Diagnostic, bundle.diagnostics)}
        for name, (model, values) in rows.items():
            _write_csv(staging / TABLES[name], model, values)
        manifest = dict(schema_version=SCHEMA_VERSION, parser_version=__version__,
                        dialect="majdataplay-pinned", reference_commits={"MajSimai": MAJSIMAI_PIN, "MajdataPlay": MAJDATAPLAY_PIN},
                        bundle_scope="single_chart", title=bundle.chart.title,
                        difficulty_index=bundle.chart.difficulty_index, chart_type=bundle.chart.chart_type,
                        source_name=bundle.chart.source_name,
                        time_origin="chart", beat_unit="quarter_note", complete=bundle.complete,
                        status="complete" if bundle.complete else "partial", tables=TABLES,
                        counts={name: len(values) for name, (_, values) in rows.items()},
                        file_sha256={file: hashlib.sha256((staging / file).read_bytes()).hexdigest() for file in TABLES.values()})
        (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        if target.exists():
            backup = Path(tempfile.mkdtemp(prefix=f".{name}-backup-", dir=root))
            backup.rmdir()
            os.rename(target, backup)
        try:
            os.rename(staging, target)
        except OSError:
            if backup is not None:
                os.rename(backup, target)
                backup = None
            raise
        if backup is not None:
            shutil.rmtree(backup)
        return target
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def read_bundle(directory: str | Path) -> ChartBundle:
    """Read and validate a bundle, preserving IDs, positions and rational strings."""
    path = Path(directory)
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA_VERSION or manifest.get("tables") != TABLES:
        raise ValueError("Unsupported bundle schema or table layout")
    if type(manifest.get("complete")) is not bool:
        raise ValueError("Manifest is missing completeness")
    for file in TABLES.values():
        if hashlib.sha256((path / file).read_bytes()).hexdigest() != manifest.get("file_sha256", {}).get(file):
            raise ValueError(f"Checksum mismatch: {file}")
    charts = _read_csv(path / "charts.csv", Chart)
    if len(charts) != 1:
        raise ValueError("Expected exactly one chart")
    bundle = ChartBundle(charts[0], _read_csv(path / "events.csv", Event),
                         _read_csv(path / "diagnostics.csv", Diagnostic), manifest["complete"])
    if manifest.get("counts") != {"charts": 1, "events": len(bundle.events), "diagnostics": len(bundle.diagnostics)}:
        raise ValueError("Manifest counts do not match tables")
    if any(manifest.get(key) != getattr(bundle.chart, key) for key in ("title", "difficulty_index", "chart_type")):
        raise ValueError("Manifest differs from chart metadata")
    validate_bundle(bundle)
    return bundle
