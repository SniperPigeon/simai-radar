#!/usr/bin/env python3
"""Build a portable Pages directory and ZIP from local analysis or a visualizer export."""

import argparse
from contextlib import contextmanager
import json
import math
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile

if sys.version_info < (3, 11):
    raise SystemExit("mairadar requires Python 3.11 or newer")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Imports must follow the Python version check and local source-path bootstrap.
from mairadar.cli import main as analyze  # noqa: E402
from mairadar.exporters.visualizer import SCHEMA_VERSION, TEMPLATE_DIRECTORY, TEMPLATE_FILES  # noqa: E402
from mairadar.io import COVER_EXTENSIONS, find_cover  # noqa: E402


@contextmanager
def site_reader(source: Path):
    """Read selected data/assets only; never unpack or execute an input site's code."""
    if source.is_dir():
        root = source.resolve()

        def read(name):
            path = root / name
            if not path.resolve().is_relative_to(root) or not path.is_file():
                raise ValueError(f"Missing or external site asset: {name}")
            return path.read_bytes()

        yield read
    else:
        with ZipFile(source) as archive:
            names = archive.namelist()
            if len(set(names)) != len(names):
                raise ValueError("Duplicate ZIP entries are not supported")

            def read(name):
                try:
                    item = archive.getinfo(name)
                except KeyError as exc:
                    raise ValueError(f"Missing site asset in ZIP: {name}") from exc
                if item.is_dir() or stat.S_ISLNK(item.external_attr >> 16):
                    raise ValueError(f"Site asset must be a regular file: {name}")
                return archive.read(item)

            yield read


def cover_name(value: str) -> str:
    if not isinstance(value, str) or "\\" in value or "\x00" in value:
        raise ValueError(f"Invalid cover path: {value!r}")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or path.as_posix() != value
        or path.parts[:2] != ("assets", "covers")
        or path.suffix.lower() not in COVER_EXTENSIONS
    ):
        raise ValueError(f"Cover must be a relative image under assets/covers/: {value}")
    return value


def validate_payload(payload: dict, *, include_covers: bool = True) -> list[dict]:
    if not isinstance(payload, dict) or payload.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError(f"Expected visualizer schema {SCHEMA_VERSION}")
    dimensions = payload.get("dimensions")
    if not isinstance(dimensions, list) or not dimensions:
        raise ValueError("No visualizer dimensions")
    keys = [dimension.get("key") for dimension in dimensions]
    if not all(isinstance(key, str) and key for key in keys) or len(set(keys)) != len(keys):
        raise ValueError("Invalid or duplicate dimension keys")
    songs = payload.get("songs")
    if not isinstance(songs, list) or not songs:
        raise ValueError("No songs to publish")
    charts = []
    for song in songs:
        if not isinstance(song.get("charts"), list) or not song["charts"]:
            raise ValueError("Every song must contain charts")
        charts.extend(song["charts"])
    stats = payload.get("stats", {})
    if stats.get("songCount") != len(songs) or stats.get("chartCount") != len(charts):
        raise ValueError("Visualizer song/chart counts do not match the data")
    if stats.get("failedChartCount") != 0 or stats.get("skippedRecordCount") != 0:
        raise ValueError("Refusing to publish failed or skipped records")
    for chart in charts:
        if chart.get("status") != "ok" or (include_covers and chart.get("exportIssues")):
            raise ValueError(f"Refusing to publish incomplete chart: {chart.get('id')}")
        for field in ("scores", "rawScores"):
            values = chart.get(field)
            if not isinstance(values, dict) or set(values) != set(keys):
                raise ValueError(f"Chart {field} must match the configured dimensions")
            for value in values.values():
                if (isinstance(value, bool) or not isinstance(value, (int, float))
                        or not math.isfinite(value)):
                    raise ValueError(f"Chart {field} must contain finite numbers")
    return charts


def restore_covers(payload: dict, target: Path, cover_root: Path) -> None:
    """Reattach raw artwork via retained sourceRef paths without running analysis."""
    from mairadar.constants import chart_type
    from mairadar.exporters.artwork import export_song_covers
    from mairadar.model import Chart
    from mairadar.reporting import AnalysisRecord

    root = cover_root.resolve(strict=True)
    records, charts = [], []
    for song in payload["songs"]:
        for chart in song["charts"]:
            ref = chart.get("sourceRef")
            if not isinstance(ref, str) or not ref or "\\" in ref:
                raise ValueError("--cover-root requires sourceRef; use the original analysis bundle")
            relative = PurePosixPath(ref)
            if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != ref:
                raise ValueError(f"Invalid sourceRef: {ref}")
            source = root / relative
            if not source.resolve().is_relative_to(root) or not source.is_file():
                raise ValueError(f"Source is missing or outside --cover-root: {ref}")
            cover = find_cover(source.parent)
            if cover is not None and not cover.resolve().is_relative_to(root):
                raise ValueError(f"Cover is outside --cover-root: {ref}")
            if cover is None and chart.get("exportIssues"):
                raise ValueError(f"Cannot repair previous cover export failure: {ref}")
            records.append(AnalysisRecord(ref, chart=Chart(
                source_name=ref, title=song["title"], artist=song.get("artist"),
                difficulty_index=chart["difficulty"], chart_type=chart_type(chart["kind"]),
            ), cover_path=cover))
            charts.append(chart)
    (target / "assets/covers").mkdir(parents=True, exist_ok=True)
    paths, issues, count = export_song_covers(records, target, Path("assets/covers"))
    if issues:
        raise ValueError(f"Cover restoration failed: {issues}")
    for index, chart in enumerate(charts):
        chart["cover"] = paths.get(index)
        # visualizer-1 exportIssues describes artwork export only. Successful
        # re-export supersedes those old failures; analysis status stays intact.
        chart["exportIssues"] = []
    for song in payload["songs"]:
        song["cover"] = next((c["cover"] for c in song["charts"] if c["cover"]), None)
    payload["stats"]["coverCount"] = count


def prepare_site(
    source: Path, target: Path, *, no_covers: bool = False,
    constants_table: Path | None = None, constant_model: Path | None = None,
    cover_root: Path | None = None,
) -> dict:
    if no_covers and cover_root is not None:
        raise ValueError("--no-covers and --cover-root cannot be combined")
    with site_reader(source) as read:
        payload = json.loads(read("data/songs.json"))
        # Cover export diagnostics do not block a package that omits artwork.
        # Keep the original diagnostics in the JSON for inspection.
        charts = validate_payload(payload, include_covers=not no_covers and cover_root is None)
        if constants_table is not None or constant_model is not None:
            from mairadar.exporters.constants import ConstantAnnotations
            failures = ConstantAnnotations(constants_table, constant_model).payload(payload)
            if failures:
                raise ValueError(f"Constant prediction failed for {failures} charts")
        if cover_root is not None:
            restore_covers(payload, target, cover_root)
            validate_payload(payload)
        covers = set()
        for record in [*payload["songs"], *charts]:
            if no_covers:
                record["cover"] = None
            elif record.get("cover") is not None:
                covers.add(cover_name(record["cover"]))
        # Source paths are useful for local diagnosis, not for a public report.
        for chart in charts:
            chart.pop("sourceRef", None)
        payload["stats"]["coverCount"] = len(covers)
        if cover_root is None:
            for name in sorted(covers):
                destination = target / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(read(name))
    for filename in TEMPLATE_FILES:
        shutil.copyfile(TEMPLATE_DIRECTORY / filename, target / filename)
    (target / "data").mkdir(exist_ok=True)
    (target / "data" / "songs.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (target / ".nojekyll").touch()
    return payload["stats"]


def build(args: argparse.Namespace) -> dict:
    output = args.output.absolute()
    archive = (args.archive or output.with_name(output.name + ".zip")).absolute()
    if output.is_symlink() or (output.exists() and (
        not output.is_dir() or any(output.iterdir())
    )):
        raise FileExistsError(f"Output must be a new or empty directory: {output}")
    if archive.exists() or archive.is_symlink():
        raise FileExistsError(f"Archive already exists: {archive}")
    if archive.resolve().is_relative_to(output.resolve()):
        raise ValueError("Archive must be outside the site directory")
    source = args.site or args.input
    if source is not None:
        if not source.exists():
            raise FileNotFoundError(f"Input does not exist: {source}")
        if (source.resolve().is_relative_to(output.resolve())
                or output.resolve().is_relative_to(source.resolve())):
            raise ValueError("Input and output paths must not contain one another")
        if archive.resolve().is_relative_to(source.resolve()):
            raise ValueError("Archive must be outside the input directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temp:
        staging = Path(temp) / "site"
        staging.mkdir()
        if args.site is None:
            generated = Path(temp) / "analysis"
            mode = "full" if args.demo else args.mode
            source = (
                ROOT / "res/examples/schema_v0.3/Schema Prototype-5-sd/maidata.txt"
                if args.demo else args.input
            )
            command = [
                "--mode", mode, "--input", str(source), "--format", "visualizer",
                "--output", str(generated),
            ]
            if args.mapping_profile:
                command += ["--mapping-profile", str(args.mapping_profile)]
            if args.difficulty:
                command += ["--difficulty", *map(str, args.difficulty)]
            if args.include_utage:
                command.append("--include-utage")
            if args.chart_type:
                command += ["--chart-type", args.chart_type]
            if analyze(command) != 0:
                raise ValueError("Analysis failed or was partial; no Pages package was created")
            source = generated
        stats = prepare_site(
            source, staging, no_covers=args.no_covers,
            constants_table=args.constants_table, constant_model=args.constant_model,
            cover_root=args.cover_root,
        )
        # Put index.html at the ZIP root for both Cloudflare drag/drop and CI reuse.
        with ZipFile(archive, "x", compression=ZIP_DEFLATED) as bundle:
            try:
                for path in sorted(staging.rglob("*")):
                    if path.is_file():
                        bundle.write(path, path.relative_to(staging).as_posix())
            except Exception:
                archive.unlink(missing_ok=True)
                raise
        try:
            if output.exists():
                output.rmdir()
            staging.rename(output)
        except Exception:
            archive.unlink(missing_ok=True)
            raise
    return {"site": str(output), "archive": str(archive), "demo": args.demo, **stats}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--site", type=Path, help="existing visualizer directory or Pages ZIP")
    source.add_argument("--input", type=Path, help="event bundles (default) or raw Simai input")
    source.add_argument("--demo", action="store_true", help="build only the synthetic example")
    parser.add_argument("--output", type=Path, default=Path("outputs/pages"))
    parser.add_argument("--archive", type=Path, help="new ZIP path; default: <output>.zip")
    parser.add_argument("--mode", choices=("full", "analysis_score"), default="analysis_score")
    parser.add_argument("--mapping-profile", type=Path)
    parser.add_argument("--constants-table", type=Path, help="attach a matched constant table")
    parser.add_argument("--constant-model", type=Path, help="predict from seven raw dimensions")
    parser.add_argument("--difficulty", type=int, nargs="+")
    parser.add_argument("--include-utage", action="store_true")
    parser.add_argument("--chart-type", choices=("dx", "sd"))
    covers = parser.add_mutually_exclusive_group()
    covers.add_argument("--no-covers", action="store_true", help="omit artwork from the package")
    covers.add_argument("--cover-root", type=Path, help="reattach artwork from this raw root using sourceRef; no analysis")
    args = parser.parse_args(argv)
    if args.site and (args.mode != "analysis_score" or args.mapping_profile or args.difficulty
                      or args.include_utage or args.chart_type):
        parser.error("--site reuses scores; analysis options require --input or --demo")
    if args.difficulty and any(value < 1 for value in args.difficulty):
        parser.error("--difficulty must contain positive inote indexes")
    try:
        print(json.dumps(build(args), ensure_ascii=False))
        return 0
    except Exception as exc:
        print(f"Pages build failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
