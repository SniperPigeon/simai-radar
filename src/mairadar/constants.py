"""OTOGE DB snapshots and exact-title joins, independent of analysis and UI.

Only explicit internal levels are labels. The site's approximate fallback from
display levels (e.g. 13+ -> 13.6) must never become a training target.
"""

from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
from urllib.request import Request, urlopen


PAGE_URL = "https://otoge-db.net/maimai/lv/"
DATA_URLS = {
    "jp": "https://otoge-db.net/maimai/data/music-ex.json",
    "intl": "https://otoge-db.net/maimai/data/music-ex-intl.json",
}
DIFFICULTIES = {2: "bas", 3: "adv", 4: "exp", 5: "mas", 6: "remas"}
CONSTANT_COLUMNS = (
    "official_constant", "match_status", "matched_chart_type", "constant_source",
    "constant_region", "constant_fetched_at", "constant_source_updated_at",
)


def chart_type(value):
    return {"dx": "dx", "sd": "sd", "std": "sd", "st": "sd"}.get(
        str(value or "").lower(), "",
    )


def finite(value):
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    with path.open("x", encoding="utf-8") as stream:
        stream.write(content)


def fetch_snapshot(region="jp", *, timeout=30):
    """Fetch once, keep provenance; callers explicitly persist/reuse the snapshot."""
    url = DATA_URLS[region]
    request = Request(url, headers={"User-Agent": "simai-radar/0.3 constant-research"})
    with urlopen(request, timeout=timeout) as response:
        songs = json.load(response)
        modified = response.headers.get("Last-Modified")
    snapshot = {
        "schema_version": "otoge-constants-1", "source_page": PAGE_URL,
        "source_url": url, "region": region,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "last_modified": modified, "songs": songs,
    }
    ConstantCatalog(snapshot)  # Reject a changed upstream schema before saving.
    return snapshot


class ConstantCatalog:
    def __init__(self, snapshot):
        if not isinstance(snapshot, dict) or snapshot.get("schema_version") != "otoge-constants-1":
            raise ValueError("Expected an otoge-constants-1 snapshot")
        if snapshot.get("region") not in DATA_URLS:
            raise ValueError("Snapshot must identify jp/intl region")
        songs = snapshot.get("songs")
        if not isinstance(songs, list) or not songs:
            raise ValueError("Snapshot songs must be a nonempty list")
        self.snapshot = snapshot
        self.by_title = defaultdict(list)
        for song in songs:
            if not isinstance(song, dict) or not isinstance(song.get("title"), str):
                raise ValueError("Invalid source song/title")
            # One source row can contain both DX and Std charts. Preserve every
            # declaration so duplicate source rows remain ambiguous.
            for kind, prefix in (("sd", ""), ("dx", "dx_")):
                if any(song.get(f"{prefix}lev_{suffix}") for suffix in DIFFICULTIES.values()):
                    self.by_title[song["title"]].append((kind, prefix, song))
        if not self.by_title:
            raise ValueError("No recognized standard charts in source snapshot")

    @classmethod
    def load(cls, path):
        return cls(json.loads(Path(path).read_text(encoding="utf-8-sig")))

    def match(self, title, difficulty_index, kind=None):
        result = dict.fromkeys(CONSTANT_COLUMNS)
        result.update(
            constant_source=self.snapshot.get("source_url"),
            constant_region=self.snapshot["region"],
            constant_fetched_at=self.snapshot.get("fetched_at"),
        )
        candidates = self.by_title.get(title, [])
        if not candidates:
            result["match_status"] = "title_not_found"
            return result
        # Type is consulted only when the exact title has multiple variants.
        if len(candidates) > 1:
            kind = chart_type(kind)
            if not kind:
                result["match_status"] = "ambiguous_type"
                return result
            candidates = [item for item in candidates if item[0] == kind]
            if len(candidates) != 1:
                result["match_status"] = "ambiguous_title" if candidates else "type_not_found"
                return result
        kind, prefix, song = candidates[0]
        result["matched_chart_type"] = kind
        result["constant_source_updated_at"] = song.get(
            "date_intl_updated" if self.snapshot["region"] == "intl" else "date_updated",
        ) or song.get("date_added")
        suffix = DIFFICULTIES.get(_difficulty(difficulty_index))
        if suffix is None:
            result["match_status"] = "unsupported_difficulty"
            return result
        key = f"{prefix}lev_{suffix}"
        if not song.get(key):
            result["match_status"] = "difficulty_not_found"
            return result
        raw = song.get(key + "_i")
        if raw is None or raw == "":
            result["match_status"] = "constant_missing"
            return result
        value = finite(raw)
        if (isinstance(raw, str) and not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", raw)) or (
            value is None or not 0 < value <= 20
        ):
            result["match_status"] = "invalid_constant"
            return result
        result.update(official_constant=value, match_status="matched")
        return result


def _difficulty(value):
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
        return number if str(number) == str(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _csv_rows(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def read_rows(source):
    """Read metadata/features, never reparse Simai or recompute a library.

    Supports exported charts.csv, visualizer data/songs.json and event-bundle
    roots (metadata only). Visualizer JSON is just an optional input adapter.
    """
    source = Path(source)
    if source.is_dir():
        if (source / "charts.csv").is_file():
            source = source / "charts.csv"
        elif (source / "data/songs.json").is_file():
            source = source / "data/songs.json"
        else:
            children = sorted(p for p in source.iterdir() if p.is_dir())
            if not children:
                raise ValueError(f"No chart bundles in {source}")
            rows = []
            for child in children:
                if not (child / "charts.csv").is_file():
                    raise ValueError(f"Bundle missing charts.csv: {child}")
                rows.extend(read_rows(child))
            return rows
    if source.suffix.lower() == ".json":
        data = json.loads(source.read_text(encoding="utf-8-sig"))
        if data.get("schemaVersion") != "mairadar-visualizer-1":
            raise ValueError("Expected visualizer songs.json or a charts CSV")
        rows = []
        for song in data["songs"]:
            for chart in song["charts"]:
                row = {
                    "title": song["title"], "difficulty_index": chart["difficulty"],
                    "chart_type": chart_type(chart.get("kind")), "level": chart.get("level"),
                    "source_ref": chart.get("sourceRef", ""), "status": chart.get("status", ""),
                }
                row.update({f"{key}_raw": value for key, value in chart.get("rawScores", {}).items()})
                rows.append(row)
    else:
        rows = _csv_rows(source)
    if not rows:
        raise ValueError(f"No charts in {source}")
    for row in rows:
        if "title" not in row or "difficulty_index" not in row:
            raise ValueError("Chart rows require title and difficulty_index")
        row["chart_type"] = chart_type(row.get("chart_type"))
    return rows


def join_constants(rows, catalog):
    return [{**row, **catalog.match(
        row["title"], row["difficulty_index"], row.get("chart_type"),
    )} for row in rows]


def write_rows(path, rows):
    if not rows:
        raise ValueError("No rows to write")
    columns = list(dict.fromkeys(key for row in rows for key in row))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


class ConstantTable:
    """Lookup only within an explicitly supplied bundle table; no song registry."""

    def __init__(self, rows):
        self.rows = defaultdict(list)
        for row in rows:
            self.rows[(row["title"], _difficulty(row["difficulty_index"]))].append(row)

    @classmethod
    def load(cls, path):
        return cls(read_rows(path))

    def lookup(self, title, difficulty, kind):
        candidates = self.rows.get((title, _difficulty(difficulty)), [])
        # This is an already-joined bundle table. Keep the original bundle type;
        # title-only fallback here could attach another chart variant's label.
        typed = [r for r in candidates if chart_type(r.get("chart_type")) == chart_type(kind)]
        candidates = typed
        if len(candidates) != 1:
            return {"officialConstant": None, "constantMatchStatus": "table_ambiguous" if candidates else "table_missing"}
        row = candidates[0]
        value = finite(row.get("official_constant")) if row.get("match_status") == "matched" else None
        return {
            "officialConstant": value, "constantMatchStatus": row.get("match_status"),
            "constantSource": row.get("constant_source"),
            "constantRegion": row.get("constant_region"),
            "constantFetchedAt": row.get("constant_fetched_at"),
        }


def main(argv=None):
    import argparse
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch", help="cache the public JSON used by the level page")
    fetch.add_argument("--region", choices=DATA_URLS, default="jp")
    fetch.add_argument("--output", type=Path, required=True)
    match = commands.add_parser("match", help="one row for every chart, including unmatched charts")
    match.add_argument("--input", type=Path, required=True)
    match.add_argument("--snapshot", type=Path, required=True)
    match.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.output.exists():
            raise FileExistsError(f"Output exists: {args.output}")
        if args.command == "fetch":
            snapshot = fetch_snapshot(args.region)
            write_json(args.output, snapshot)
            print(json.dumps({"songs": len(snapshot["songs"]), "output": str(args.output)}))
            return 0
        rows = join_constants(read_rows(args.input), ConstantCatalog.load(args.snapshot))
        write_rows(args.output, rows)
        counts = Counter(row["match_status"] for row in rows)
        print(json.dumps({"charts": len(rows), "matches": counts, "output": str(args.output)}, ensure_ascii=False))
        return int(counts["matched"] != len(rows))
    except Exception as exc:
        print(f"Constant collection failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
