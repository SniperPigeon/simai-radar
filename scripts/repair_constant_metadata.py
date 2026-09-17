#!/usr/bin/env python3
"""Apply a reviewed, one-time metadata plan to raw files and a copied analysis bundle.

The plan lives under data/ or outputs/. No fuzzy matching, library scan, parser
changes, event edits, feature recomputation, or persistent song registry.
"""

import argparse
from copy import deepcopy
import json
from pathlib import Path, PurePosixPath
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mairadar.constants import chart_type, write_json


def repair_metadata(text, changes):
    """Preserve BOM, line endings and all bytes outside reviewed scalar values."""
    edits = []
    for field, change in changes.items():
        if field not in {"title", "artist", "cabinet", "cabinate"}:
            raise ValueError(f"Unsupported metadata field: {field}")
        before, after = change["before"], change["after"]
        if (before is not None and not isinstance(before, str)) or not isinstance(after, str):
            raise ValueError("Metadata values must be strings; before may be null for a new field")
        if any(c in after for c in "\r\n"):
            raise ValueError("Metadata replacements must occupy one line")
        if field in {"cabinet", "cabinate"} and after not in {"dx", "sd"}:
            raise ValueError("Cabinet repair must explicitly choose dx or sd")
        pattern = re.compile(rf"(?m)^(?:\ufeff)?&{field}=([^\r\n]*)")
        matches = list(pattern.finditer(text))
        if len(matches) > 1:
            raise ValueError(f"Duplicate &{field}; review manually")
        actual = matches[0][1] if matches else None
        if actual == after:
            continue
        if actual != before:
            raise ValueError(f"Unexpected &{field}: expected {before!r}, got {actual!r}")
        if matches:
            match = matches[0]
            text = text[:match.start(1)] + after + text[match.end(1):]
        else:
            newline = "\r\n" if "\r\n" in text else "\n"
            offset = int(text.startswith("\ufeff"))
            text = text[:offset] + f"&{field}={after}{newline}" + text[offset:]
        edits.append({"field": field, "before": before, "after": after})
    return text, edits


def _relative_source(value):
    if not isinstance(value, str) or "\\" in value:
        raise ValueError("source_ref must be a relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise ValueError(f"Invalid source_ref: {value}")
    return Path(value)


def repair_payload(payload, entries):
    result = deepcopy(payload)
    if result.get("schemaVersion") != "mairadar-visualizer-1":
        raise ValueError("Expected a visualizer bundle with sourceRef metadata")
    changes = []
    for entry in entries:
        found = 0
        for song in result["songs"]:
            charts = [c for c in song["charts"] if c.get("sourceRef") == entry["source_ref"]]
            if not charts:
                continue
            if len(charts) != len(song["charts"]):
                raise ValueError("Mixed sources within a song; cannot change song-level metadata")
            for field in ("title", "artist"):
                change = entry["metadata"].get(field)
                if change:
                    if song.get(field) not in {change["before"], change["after"]}:
                        raise ValueError(f"Unexpected cached {field}: {entry['source_ref']}")
                    song[field] = change["after"]
            kind = entry["metadata"].get("cabinet", entry["metadata"].get("cabinate"))
            for chart in charts:
                found += 1
                if kind:
                    before = entry.get("chart_types_before", {}).get(str(chart["difficulty"]))
                    if before is None:
                        raise ValueError("Every affected cached difficulty needs chart_types_before")
                    after = kind["after"]
                    if chart_type(chart["kind"]) not in {before, after}:
                        raise ValueError(f"Unexpected cached type: {entry['source_ref']}")
                    chart["kind"] = "DX" if after == "dx" else "ST"
                    chart["kindLabel"] = "DX谱" if after == "dx" else "标谱"
                # Invalidate optional annotations computed for the old metadata.
                for key in ("officialConstant", "constantMatchStatus", "constantSource", "constantRegion",
                            "constantFetchedAt", "displayConstant", "constantValueKind",
                            "constantSourceKind", "constantDeletedDate"):
                    chart.pop(key, None)
            song["chartKinds"] = list(dict.fromkeys(c["kind"] for c in song["charts"]))
        if not found:
            raise ValueError(f"No cached charts for {entry['source_ref']}")
        changes.append({"source_ref": entry["source_ref"], "cached_charts": found})
    return result, changes


def run(root, site, plan, output, backup_root, *, apply=False):
    root, site, output, backup_root = map(Path, (root, site, output, backup_root))
    if plan.get("schema_version") != "mairadar-metadata-repairs-1":
        raise ValueError("Unsupported metadata repair plan")
    entries = plan.get("repairs")
    if not isinstance(entries, list) or not entries:
        raise ValueError("No reviewed metadata repairs")
    if len({e["source_ref"] for e in entries}) != len(entries):
        raise ValueError("Duplicate repair source paths")
    data_path = site / "data/songs.json" if site.is_dir() else site
    payload = json.loads(data_path.read_text(encoding="utf-8-sig"))
    repaired, cached_changes = repair_payload(payload, entries)
    planned = []
    for entry in entries:
        relative = _relative_source(entry["source_ref"])
        path = root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"Source escapes raw root: {path}")
        original = path.read_bytes()
        fixed, edits = repair_metadata(original.decode("utf-8"), entry["metadata"])
        repeated, repeated_edits = repair_metadata(fixed, entry["metadata"])
        if repeated != fixed or repeated_edits:
            raise ValueError("Metadata repair must converge in one pass")
        if edits:
            planned.append((relative, path, original, fixed.encode("utf-8"), edits))
    report = {
        "applied": apply, "raw_files_changed": len(planned),
        "raw_changes": [{"source_ref": p.as_posix(), "edits": edits}
                        for p, _, _, _, edits in planned],
        "cached_changes": cached_changes, "features_recomputed": False,
        "source_bundle": str(site), "backup_root": str(backup_root),
    }
    if not apply:
        return report
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Repair output must be a new directory: {output}")
    if output.resolve().is_relative_to(root.resolve()) or root.resolve().is_relative_to(output.resolve()):
        raise ValueError("Repair output and raw root must not contain one another")
    # Check every source and backup before the first mutation, then back up all
    # original files before changing any raw metadata.
    for relative, path, original, _, _ in planned:
        backup = backup_root / relative
        if (backup.exists() or backup.is_symlink()) and (
            backup.is_symlink() or not backup.is_file() or backup.read_bytes() != original
        ):
            raise ValueError(f"Existing backup differs: {backup}")
        if backup.resolve().is_relative_to(root.resolve()):
            raise ValueError("Backups must be outside the raw root")
    for relative, _, original, _, _ in planned:
        backup = backup_root / relative
        backup.parent.mkdir(parents=True, exist_ok=True)
        if not backup.exists():
            with backup.open("xb") as stream:
                stream.write(original)
    for _, path, original, fixed, _ in planned:
        if path.read_bytes() != original:
            raise ValueError(f"Raw file changed during repair: {path}")
        path.write_bytes(fixed)
    write_json(output / "data/songs.json", repaired)
    write_json(output / "repair-report.json", report)
    write_json(output / "repair-plan.json", plan)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/raw"))
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backup-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="default: preview only")
    args = parser.parse_args(argv)
    try:
        result = run(args.root, args.site, json.loads(args.plan.read_text(encoding="utf-8")),
                     args.output, args.backup_root, apply=args.apply)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"Metadata repair failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
