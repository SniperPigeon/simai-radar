"""OTOGE DB snapshots and normalized-title joins, independent of analysis and UI.

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
import unicodedata
from urllib.request import Request, urlopen


PAGE_URL = "https://otoge-db.net/maimai/lv/"
DATA_URLS = (
    "https://otoge-db.net/maimai/data/music-ex.json",
    "https://raw.githubusercontent.com/zvuc/otoge-db/main/maimai/data/music-ex-deleted.json",
)
DIFFICULTIES = {2: "bas", 3: "adv", 4: "exp", 5: "mas", 6: "remas"}
CONSTANT_COLUMNS = (
    "official_constant", "match_status", "matched_chart_type", "constant_source",
    "constant_fetched_at", "constant_source_updated_at",
    "matched_title", "matched_artist", "title_match_method",
    "display_constant", "constant_value_kind",
)
LEGACY_COLUMNS = {"constant_region", "constant_source_kind", "constant_deleted_date"}


def comparison_text(value):
    """Ignore punctuation/symbol/spacing differences, retaining letters and case."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    return "".join(c for c in text if (
        unicodedata.category(c)[0] not in "PSZ"
        and unicodedata.category(c) != "Cf" and not c.isspace()
    ))


def title_key(value):
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"\s*\[(?:DX|ST|STD|SD)\]\s*$", "", text, flags=re.IGNORECASE)
    # Symbol-only names still require an exact match. Never match empty keys.
    return comparison_text(text)


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


def _fetch_source(url, timeout):
    request = Request(url, headers={"User-Agent": "simai-radar/0.3 constant-research"})
    with urlopen(request, timeout=timeout) as response:
        songs = json.load(response)
        modified = response.headers.get("Last-Modified")
    return {
        "source_url": url,
        "last_modified": modified, "songs": songs,
    }


def fetch_snapshot(*, timeout=30):
    """Always fetch both Japanese datasets, with no region or availability filter."""
    snapshot = {
        "schema_version": "otoge-constants-2", "source_page": PAGE_URL,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "sources": [_fetch_source(url, timeout) for url in DATA_URLS],
    }
    ConstantCatalog(snapshot)  # Reject a changed upstream schema before saving.
    return snapshot


class _CatalogSource:
    def __init__(self, snapshot):
        if not isinstance(snapshot, dict):
            raise ValueError("Invalid constant source")
        songs = snapshot.get("songs")
        if not isinstance(songs, list) or not songs:
            raise ValueError("Snapshot songs must be a nonempty list")
        self.snapshot = snapshot
        self.by_title = defaultdict(list)
        self.by_title_key = defaultdict(list)
        for song in songs:
            if not isinstance(song, dict) or not isinstance(song.get("title"), str):
                raise ValueError("Invalid source song/title")
            # One source row can contain both DX and Std charts. Preserve every
            # declaration so duplicate source rows remain ambiguous.
            for kind, prefix in (("sd", ""), ("dx", "dx_")):
                if any(song.get(f"{prefix}lev_{suffix}") for suffix in DIFFICULTIES.values()):
                    candidate = (kind, prefix, song)
                    self.by_title[song["title"]].append(candidate)
                    if title_key(song["title"]):
                        self.by_title_key[title_key(song["title"])].append(candidate)
        if not self.by_title:
            raise ValueError("No recognized standard charts in source snapshot")

    def match(self, title, difficulty_index, kind=None, artist=None):
        result = dict.fromkeys(CONSTANT_COLUMNS)
        result.update(
            constant_source=self.snapshot.get("source_url"),
            constant_fetched_at=self.snapshot.get("fetched_at"),
        )
        candidates = self.by_title.get(title, [])
        result["title_match_method"] = "exact" if candidates else "normalized"
        if not candidates:
            candidates = self.by_title_key.get(title_key(title), []) if title_key(title) else []
        if not candidates:
            result["match_status"] = "title_not_found"
            result["title_match_method"] = None
            return result
        # Type/artist only disambiguate candidates; they are not required when
        # a unique source chart variant already matches the title.
        if len(candidates) > 1:
            kind = chart_type(kind)
            if kind:
                candidates = [item for item in candidates if item[0] == kind]
            if not candidates:
                result["match_status"] = "type_not_found"
                return result
            if len(candidates) > 1 and comparison_text(artist):
                candidates = [item for item in candidates
                              if comparison_text(item[2].get("artist")) == comparison_text(artist)]
                if not candidates:
                    result["match_status"] = "artist_not_found"
                    return result
            if len(candidates) != 1:
                result["match_status"] = (
                    "ambiguous_type" if len({item[0] for item in candidates}) > 1
                    else "ambiguous_title"
                )
                return result
        kind, prefix, song = candidates[0]
        result["matched_chart_type"] = kind
        result["matched_title"] = song["title"]
        result["matched_artist"] = song.get("artist")
        result["constant_source_updated_at"] = song.get("date_updated") or song.get("date_added")
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
            # Mirror the page's explicit approximate display, but never promote
            # that approximation to an official constant or a training target.
            level = str(song[key])
            if re.fullmatch(r"[0-9]+\+?", level):
                estimate = float(level.replace("+", ".6"))
                if 0 < estimate <= 20:
                    result.update(display_constant=estimate, constant_value_kind="display_estimate",
                                  match_status="constant_estimated")
            return result
        value = finite(raw)
        if (isinstance(raw, str) and not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", raw)) or (
            value is None or not 0 < value <= 20
        ):
            result["match_status"] = "invalid_constant"
            return result
        result.update(official_constant=value, display_constant=value,
                      constant_value_kind="explicit", match_status="matched")
        return result


class ConstantCatalog:
    """Search Japanese sources in order; any unambiguous explicit label is usable."""

    def __init__(self, snapshot):
        if not isinstance(snapshot, dict):
            raise ValueError("Expected a constant snapshot")
        if snapshot.get("schema_version") == "otoge-constants-2":
            sources = snapshot.get("sources")
            if not isinstance(sources, list) or not sources:
                raise ValueError("Snapshot requires sources")
        elif snapshot.get("schema_version") == "otoge-constants-1":
            # Previously downloaded snapshots stay usable; obsolete metadata is ignored.
            sources = [snapshot]
            if snapshot.get("deleted_snapshot") is not None:
                sources.append(snapshot["deleted_snapshot"])
        else:
            raise ValueError("Expected an otoge-constants-1/2 snapshot")
        self.sources = []
        for source in sources:
            if not isinstance(source, dict):
                raise ValueError("Invalid constant source")
            self.sources.append(_CatalogSource({
                **source, "fetched_at": source.get("fetched_at", snapshot.get("fetched_at")),
            }))

    @classmethod
    def load(cls, path):
        return cls(json.loads(Path(path).read_text(encoding="utf-8-sig")))

    def match(self, title, difficulty_index, kind=None, artist=None):
        candidates = [(index, candidate) for index, source in enumerate(self.sources)
                      for candidate in source.by_title.get(title, [])]
        method = "exact"
        if not candidates and title_key(title):
            method = "normalized"
            candidates = [(index, candidate) for index, source in enumerate(self.sources)
                          for candidate in source.by_title_key.get(title_key(title), [])]
        if not candidates:
            return self.sources[0].match(title, difficulty_index, kind, artist)

        def variants(items):
            return {(item[1][2]["title"], comparison_text(item[1][2].get("artist")), item[1][0])
                    for item in items}

        def failure(status):
            return {**dict.fromkeys(CONSTANT_COLUMNS), "match_status": status,
                    "title_match_method": method}

        # Resolve title variants across ALL sources. Repeated appearances of the
        # same variant in different sources do not create another song identity.
        if len(variants(candidates)) > 1 and chart_type(kind):
            candidates = [item for item in candidates if item[1][0] == chart_type(kind)]
            if not candidates:
                return failure("type_not_found")
        if len(variants(candidates)) > 1 and comparison_text(artist):
            candidates = [item for item in candidates
                          if comparison_text(item[1][2].get("artist")) == comparison_text(artist)]
            if not candidates:
                return failure("artist_not_found")
        if len(variants(candidates)) > 1:
            return failure("ambiguous_type" if len({item[1][0] for item in candidates}) > 1 else "ambiguous_title")

        results = []
        for index, source in enumerate(self.sources):
            available = [candidate for source_index, candidate in candidates if source_index == index]
            if not available:
                continue
            # Retain ambiguity for duplicate declarations within a single source.
            if len(available) != 1:
                results.append(failure("ambiguous_title"))
                continue
            selected_kind, _, song = available[0]
            result = source.match(song["title"], difficulty_index, selected_kind, song.get("artist"))
            result["title_match_method"] = method
            results.append(result)
        # The first explicit value wins; an estimate never hides an explicit
        # value from another source. Missing values remain missing if all fail.
        return next((result for result in results if result["match_status"] == "matched"), results[0])


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
        if data.get("schemaVersion") != "mairadar-visualizer-2":
            raise ValueError("Expected visualizer songs.json or a charts CSV")
        rows = []
        for song in data["songs"]:
            for chart in song["charts"]:
                row = {
                    "title": song["title"], "artist": song.get("artist", ""),
                    "difficulty_index": chart["difficulty"],
                    "chart_type": chart_type(chart.get("kind")), "level": chart.get("level"),
                    "source_ref": chart.get("sourceRef", ""), "status": chart.get("status", ""),
                }
                row.update({f"{key}_raw": value for key, value in chart["rawFeatures"].items()})
                rows.append(row)
    else:
        rows = _csv_rows(source)
    if not rows:
        raise ValueError(f"No charts in {source}")
    for row in rows:
        if "title" not in row or "difficulty_index" not in row:
            raise ValueError("Chart rows require title and difficulty_index")
        row["chart_type"] = chart_type(row.get("chart_type"))
        for key in LEGACY_COLUMNS:
            row.pop(key, None)
    return rows


def join_constants(rows, catalog):
    return [{**{k: v for k, v in row.items() if k not in LEGACY_COLUMNS}, **catalog.match(
        row["title"], row["difficulty_index"], row.get("chart_type"), row.get("artist"),
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

    def lookup(self, title, difficulty, kind, artist=None):
        candidates = self.rows.get((title, _difficulty(difficulty)), [])
        # This is an already-joined bundle table. Keep the original bundle type;
        # title-only fallback here could attach another chart variant's label.
        typed = [r for r in candidates if chart_type(r.get("chart_type")) == chart_type(kind)]
        candidates = typed
        if len(candidates) > 1 and comparison_text(artist):
            candidates = [r for r in candidates
                          if comparison_text(r.get("artist")) == comparison_text(artist)]
        if len(candidates) != 1:
            return {"officialConstant": None, "constantMatchStatus": "table_ambiguous" if candidates else "table_missing"}
        row = candidates[0]
        value = finite(row.get("official_constant")) if row.get("match_status") == "matched" else None
        return {
            "officialConstant": value, "constantMatchStatus": row.get("match_status"),
            "displayConstant": finite(row.get("display_constant")),
            "constantValueKind": row.get("constant_value_kind"),
            "constantSource": row.get("constant_source"),
            "constantFetchedAt": row.get("constant_fetched_at"),
        }


def main(argv=None):
    import argparse
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch", help="cache all available Japanese constant sources")
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
            snapshot = fetch_snapshot()
            write_json(args.output, snapshot)
            print(json.dumps({"source_records": sum(len(s["songs"]) for s in snapshot["sources"]),
                              "output": str(args.output)}))
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
