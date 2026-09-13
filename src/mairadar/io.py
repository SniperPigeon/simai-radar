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
COVER_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
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
    return bundles


def is_empty_chart(bundle: ChartBundle) -> bool:
    """Skip empty inputs at the batch boundary, without hiding malformed notes."""
    return not any(event.kind != "timing" for event in bundle.events) and all(
        diagnostic.severity != "error" or diagnostic.code == "EMPTY_CHART"
        for diagnostic in bundle.diagnostics
    )


def bundle_directory_name(chart: Chart) -> str:
    """Human-readable export name, never a song identity or deduplication key."""
    # Only filesystem-unsafe characters change; original metadata stays intact.
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", chart.title or "").strip().rstrip(".")
    return f"{title}-{chart.difficulty_index or ''}-{chart.chart_type or ''}"


def find_cover(directory: str | Path) -> Path | None:
    """Optional file-adapter convention, in deterministic preference order."""
    root = Path(directory)
    for name in ("bg.png", "bg.jpg", "bg.jpeg", "bg.webp"):
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def _cover_name(manifest: dict) -> str | None:
    assets = manifest.get("assets", {})
    if not isinstance(assets, dict) or set(assets) - {"cover"}:
        raise ValueError("Unsupported bundle assets")
    name = assets.get("cover")
    if name is not None and (not isinstance(name, str) or name not in {
        "cover" + suffix for suffix in COVER_EXTENSIONS
    }):
        raise ValueError("Invalid cover asset path")
    return name


def _read_manifest(directory: Path) -> dict:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION or manifest.get("tables") != TABLES:
        raise ValueError("Unsupported bundle schema or table layout")
    if type(manifest.get("complete")) is not bool:
        raise ValueError("Manifest is missing completeness")
    _cover_name(manifest)
    return manifest


def _check_file(directory: Path, filename: str, manifest: dict) -> Path:
    path = directory / filename
    if path.is_symlink():
        raise ValueError(f"Bundle file must not be a symlink: {filename}")
    if hashlib.sha256(path.read_bytes()).hexdigest() != manifest.get("file_sha256", {}).get(filename):
        raise ValueError(f"Checksum mismatch: {filename}")
    return path


def read_cover_path(directory: str | Path) -> Path | None:
    """Resolve and integrity-check the optional packaged cover, without decoding it."""
    root = Path(directory)
    manifest = _read_manifest(root)
    name = _cover_name(manifest)
    return _check_file(root, name, manifest) if name else None


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


def write_bundle(bundle: ChartBundle, output: str | Path, *, overwrite: bool = False,
                 cover_path: str | Path | None = None) -> Path:
    """Write one chart directory, staging all files before replacing old output.

    Replacement is allowed only for a same-format generated bundle, with no extra
    user files. On rename failure the previous directory is restored. An optional
    cover is copied byte-for-byte into the staging directory; no parser/model
    fields or source files are changed. None exports no cover, even on overwrite.
    """
    validate_bundle(bundle)
    name = bundle_directory_name(bundle.chart)
    cover = Path(cover_path) if cover_path is not None else None
    cover_name = None
    if cover is not None:
        if cover.suffix.lower() not in COVER_EXTENSIONS:
            raise ValueError("Cover must be a JPG, JPEG, PNG or WebP file")
        if not cover.is_file():
            raise FileNotFoundError(f"Cover not found: {cover}")
        cover_name = "cover" + cover.suffix.lower()
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    target = root / name
    if target.is_symlink():
        raise ValueError(f"Refusing symlink output: {target}")
    if target.exists():
        if not overwrite:
            raise FileExistsError(f"Output exists: {target}; use --overwrite to replace this bundle")
        old = _read_manifest(target)
        old_cover = _cover_name(old)
        expected = set(TABLES.values()) | {"manifest.json"}
        if old_cover:
            expected.add(old_cover)
        if {p.name for p in target.iterdir()} != expected:
            raise ValueError("Output contains extra files; refusing replacement")
    staging = Path(tempfile.mkdtemp(prefix=f".{name}-", dir=root))
    backup = None
    try:
        rows = {"charts": (Chart, [bundle.chart]), "events": (Event, bundle.events),
                "diagnostics": (Diagnostic, bundle.diagnostics)}
        for table, (model, values) in rows.items():
            _write_csv(staging / TABLES[table], model, values)
        assets = {}
        files = list(TABLES.values())
        if cover is not None:
            shutil.copyfile(cover, staging / cover_name)
            assets["cover"] = cover_name
            files.append(cover_name)
        manifest = dict(schema_version=SCHEMA_VERSION, parser_version=__version__,
                        dialect="majdataplay-pinned", reference_commits={"MajSimai": MAJSIMAI_PIN, "MajdataPlay": MAJDATAPLAY_PIN},
                        bundle_scope="single_chart", title=bundle.chart.title,
                        difficulty_index=bundle.chart.difficulty_index, chart_type=bundle.chart.chart_type,
                        source_name=bundle.chart.source_name,
                        time_origin="chart", beat_unit="quarter_note", complete=bundle.complete,
                        status="complete" if bundle.complete else "partial", tables=TABLES, assets=assets,
                        counts={name: len(values) for name, (_, values) in rows.items()},
                        file_sha256={file: hashlib.sha256((staging / file).read_bytes()).hexdigest() for file in files})
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
    manifest = _read_manifest(path)
    for file in TABLES.values():
        _check_file(path, file, manifest)
    cover_name = _cover_name(manifest)
    if cover_name:
        _check_file(path, cover_name, manifest)
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
