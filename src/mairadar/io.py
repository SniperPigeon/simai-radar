"""Validated CSV bundle I/O. JSON cells are decoded here, outside analysis."""

from dataclasses import asdict, fields
from fractions import Fraction
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid

from . import __version__
from .model import (Chart, ChartBundle, Diagnostic, Event, MAJDATAPLAY_PIN,
                    MAJSIMAI_PIN, SCHEMA_VERSION)

TABLES = {"charts": "charts.csv", "events": "events.csv", "diagnostics": "diagnostics.csv"}
JSON_FIELDS = {"metadata_json", "slide_path_json", "flags_json", "recovery_json"}
BOOL_FIELDS = {"is_slide_head", "is_break", "is_ex", "is_mine"}
INT_FIELDS = {"difficulty_index", "source_start", "source_end", "source_line", "source_column"}
FLOAT_FIELDS = {"slide_declare_time_s", "start_time_s", "end_time_s", "bpm", "offset_s",
                "chart_end_time_s", "last_event_end_s", "audio_duration_s"}
NULLABLE = {"difficulty_index", "difficulty_label", "level_text", "title", "artist", "designer",
            "timing_type", "start_beat", "end_beat", "position", "head_event_id"} | BOOL_FIELDS | FLOAT_FIELDS


def validate_bundle(bundle: ChartBundle) -> None:
    """Check the public model invariants, including shared heads and inline paths."""
    chart = bundle.chart
    if not re.fullmatch(r"[A-Za-z0-9_-]+", chart.chart_id):
        raise ValueError("Unsafe chart_id")
    if not re.fullmatch(r"[0-9a-f]{64}", chart.source_sha256):
        raise ValueError("Invalid source SHA-256")
    events = {e.event_id: e for e in bundle.events}
    if len(events) != len(bundle.events):
        raise ValueError("Duplicate event_id")
    def finite(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    for key in FLOAT_FIELDS:
        value = getattr(chart, key, None)
        if value is not None and not finite(value):
            raise ValueError(f"Non-finite chart field: {key}")
    for event in bundle.events:
        if event.chart_id != chart.chart_id or event.kind not in {"tap", "hold", "touch", "touch_hold", "slide", "timing"}:
            raise ValueError("Invalid event identity/type")
        if not finite(event.start_time_s) or not finite(event.end_time_s) or not 0 <= event.start_time_s <= event.end_time_s:
            raise ValueError("Invalid event interval")
        for beat in (event.start_beat, event.end_beat):
            if beat is not None and (not isinstance(beat, str) or str(Fraction(beat)) != beat):
                raise ValueError("Beat must be a canonical rational string")
        if event.kind in {"tap", "touch", "timing"} and event.start_time_s != event.end_time_s:
            raise ValueError("Point event has nonzero duration")
        if not (0 <= event.source_start < event.source_end and event.source_line >= 1 and event.source_column >= 1):
            raise ValueError("Invalid source span")
        if len(event.raw_token) != event.source_end - event.source_start:
            raise ValueError("Source span length differs from raw_token")
        if event.kind == "timing":
            if event.timing_type != "bpm" or not finite(event.bpm) or event.bpm <= 0:
                raise ValueError("Invalid BPM event")
            if any(getattr(event, key) is not None for key in BOOL_FIELDS | {"position", "head_event_id", "slide_path_json", "flags_json", "slide_declare_time_s"}):
                raise ValueError("BPM event has note-only fields")
            continue
        if event.timing_type is not None or event.bpm is not None:
            raise ValueError("Note has BPM-only fields")
        if any(type(getattr(event, key)) is not bool for key in BOOL_FIELDS) or not isinstance(event.flags_json, dict):
            raise ValueError("Note flags must be booleans and a JSON object")
        touch = event.kind in {"touch", "touch_hold"}
        pattern = r"(?:[ABDE][1-8]|C)" if touch else r"[1-8]"
        if not isinstance(event.position, str) or not re.fullmatch(pattern, event.position):
            raise ValueError("Invalid string position")
        if event.is_slide_head and event.kind != "tap":
            raise ValueError("Only a Tap can be a Slide head")
        if event.kind != "slide":
            if any(getattr(event, key) is not None for key in ("slide_declare_time_s", "head_event_id", "slide_path_json")):
                raise ValueError("Non-Slide has path fields")
            continue
        if not finite(event.slide_declare_time_s) or not 0 <= event.slide_declare_time_s <= event.start_time_s:
            raise ValueError("Every Slide requires a valid declaration time")
        if event.head_event_id:
            head = events.get(event.head_event_id)
            if head is None or not head.is_slide_head or head.position != event.position or head.start_time_s != event.slide_declare_time_s:
                raise ValueError("Invalid shared Slide head reference")
        path = event.slide_path_json
        if not isinstance(path, list) or not path:
            if bundle.complete:
                raise ValueError("Complete Slide has no path")
            continue
        previous = event.position
        previous_time = event.start_time_s
        for segment in path:
            if set(segment) != {"shape", "start_position", "via_position", "end_position", "start_time_s", "end_time_s", "raw_segment", "time_resolution"}:
                raise ValueError("Unexpected Slide segment fields")
            if segment["start_position"] != previous:
                raise ValueError("Disconnected Slide segments")
            if segment["shape"] not in {"-", "^", "v", "<", ">", "V", "p", "q", "pp", "qq", "s", "z", "w"}:
                raise ValueError("Unknown Slide shape")
            for key in ("start_position", "end_position"):
                if not isinstance(segment[key], str) or not re.fullmatch(r"[1-8]", segment[key]):
                    raise ValueError("Invalid segment position")
            via = segment["via_position"]
            if (segment["shape"] == "V") != (via is not None):
                raise ValueError("Only V requires an explicit via position")
            if via is not None and (not isinstance(via, str) or not re.fullmatch(r"[1-8]", via)):
                raise ValueError("Invalid via position")
            start, end = segment["start_time_s"], segment["end_time_s"]
            if start is None or end is None:
                if start is not None or end is not None or segment["time_resolution"] != "needs_geometry" or bundle.complete:
                    raise ValueError("Unresolved segment must mark bundle incomplete")
            else:
                if segment["time_resolution"] != "explicit_duration":
                    raise ValueError("Unexpected segment time resolution")
                if not finite(start) or not finite(end) or not event.start_time_s <= start <= end <= event.end_time_s or start != previous_time:
                    raise ValueError("Invalid segment interval")
                previous_time = end
            previous = segment["end_position"]
        if all(s["end_time_s"] is not None for s in path) and previous_time != event.end_time_s:
            raise ValueError("Segments do not cover Slide duration")
    for diagnostic in bundle.diagnostics:
        if diagnostic.chart_id != chart.chart_id or diagnostic.severity not in {"error", "warning", "info"}:
            raise ValueError("Invalid diagnostic")
        if not isinstance(diagnostic.recovery_json, dict):
            raise ValueError("Diagnostic recovery must be a JSON object")
    if bundle.complete and any(d.severity == "error" for d in bundle.diagnostics):
        raise ValueError("Complete bundle contains errors")
    if bundle.complete and (chart.chart_end_time_s is None or chart.offset_s is None):
        raise ValueError("Complete chart lacks time metadata")
    last = max((e.end_time_s for e in bundle.events if e.kind != "timing"), default=None)
    if chart.last_event_end_s != last:
        raise ValueError("last_event_end_s differs from note endpoints")


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

    Replacement is allowed only for a matching generated bundle, with no extra
    user files. On rename failure the previous directory is restored.
    """
    validate_bundle(bundle)
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    target = root / bundle.chart.chart_id
    if target.is_symlink():
        raise ValueError(f"Refusing symlink output: {target}")
    if target.exists():
        if not overwrite:
            raise FileExistsError(f"Output exists: {target}; use --overwrite to replace this bundle")
        old = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        if old.get("chart_id") != bundle.chart.chart_id or old.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("Refusing to replace an unrelated output directory")
        if {p.name for p in target.iterdir()} != set(TABLES.values()) | {"manifest.json"}:
            raise ValueError("Output contains extra files; refusing replacement")
    staging = Path(tempfile.mkdtemp(prefix=f".{bundle.chart.chart_id}-", dir=root))
    backup = None
    try:
        rows = {"charts": (Chart, [bundle.chart]), "events": (Event, bundle.events),
                "diagnostics": (Diagnostic, bundle.diagnostics)}
        for name, (model, values) in rows.items():
            _write_csv(staging / TABLES[name], model, values)
        manifest = dict(schema_version=SCHEMA_VERSION, parser_version=__version__,
                        dialect="majdataplay-pinned", reference_commits={"MajSimai": MAJSIMAI_PIN, "MajdataPlay": MAJDATAPLAY_PIN},
                        bundle_scope="single_chart", chart_id=bundle.chart.chart_id,
                        source_name=bundle.chart.source_name, source_sha256=bundle.chart.source_sha256,
                        time_origin="chart", beat_unit="quarter_note", complete=bundle.complete,
                        status="complete" if bundle.complete else "partial", tables=TABLES,
                        counts={name: len(values) for name, (_, values) in rows.items()},
                        file_sha256={file: hashlib.sha256((staging / file).read_bytes()).hexdigest() for file in TABLES.values()})
        (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        if target.exists():
            backup = root / f".{bundle.chart.chart_id}-backup-{uuid.uuid4().hex}"
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
    if manifest.get("chart_id") != bundle.chart.chart_id or manifest.get("source_sha256") != bundle.chart.source_sha256:
        raise ValueError("Manifest identity differs from chart metadata")
    validate_bundle(bundle)
    return bundle
