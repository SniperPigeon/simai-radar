"""Raw-source to event-bundle batch adapter, independent of analysis and scoring."""

from dataclasses import dataclass, field
from pathlib import Path

from .chart_type import detect_chart_type
from .io import find_cover, is_empty_chart, parse_file, write_bundle
from .model import Chart, ParseResult


@dataclass(frozen=True)
class ParseOnlyIssue:
    code: str
    message: str


@dataclass
class ParseOnlyRecord:
    source_name: str
    chart: Chart | None = None
    bundle_path: Path | None = None
    complete: bool = False
    diagnostics: list[ParseOnlyIssue] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.bundle_path is None:
            return "error"
        return "ok" if self.complete and not self.diagnostics else "partial"


@dataclass
class ParseOnlyResult:
    output_path: Path
    records: list[ParseOnlyRecord]

    @property
    def exported_bundles(self) -> int:
        return sum(record.bundle_path is not None for record in self.records)

    @property
    def failed_records(self) -> int:
        return sum(record.status != "ok" for record in self.records)

    @property
    def exit_code(self) -> int:
        return int(not self.records or self.failed_records > 0)


def parse_source_to_bundles(
    source: str | Path,
    output: str | Path,
    *,
    difficulties: list[int] | None = None,
    chart_type: str | None = None,
) -> ParseOnlyResult:
    """Parse raw files once and persist independent event bundles without analysis."""
    source = Path(source)
    if chart_type not in {None, "dx", "sd"}:
        raise ValueError("chart_type must be dx or sd")
    if source.is_file():
        root, files = source.parent, [source]
    elif source.is_dir():
        root = source
        files = sorted(path for path in root.rglob("*") if path.is_file() and (
            path.name.lower() in {"maidata.txt", "majdata.txt"} or path.suffix.lower() == ".simai"
        ))
    else:
        raise ValueError(f"Input must be a Simai file or directory: {source}")

    output = Path(output).absolute()
    records = []
    for path in files:
        name = path.relative_to(root).as_posix()
        try:
            bundles = parse_file(path, source_name=name, difficulties=difficulties)
        except Exception as exc:
            records.append(ParseOnlyRecord(
                name,
                diagnostics=[ParseOnlyIssue("PARSE_FAILED", str(exc))],
            ))
            continue
        if not bundles:
            records.append(ParseOnlyRecord(
                name,
                diagnostics=[ParseOnlyIssue("NO_CHARTS", "No requested inote charts found")],
            ))
            continue
        cover_path = find_cover(path.parent)
        for bundle in bundles:
            if is_empty_chart(bundle):
                continue
            record = ParseOnlyRecord(name, chart=bundle.chart, complete=bundle.complete)
            records.append(record)
            try:
                if chart_type is not None:
                    bundle.chart.chart_type = chart_type
                elif bundle.chart.chart_type is None:
                    bundle.chart.chart_type = detect_chart_type(ParseResult(
                        bundle.events,
                        bundle.diagnostics,
                        bundle.complete,
                        bundle.chart.chart_end_time_s,
                        bundle.chart.last_event_end_s,
                    ))
                record.bundle_path = write_bundle(bundle, output, cover_path=cover_path)
            except Exception as exc:
                record.diagnostics.append(ParseOnlyIssue("BUNDLE_EXPORT_FAILED", str(exc)))
    return ParseOnlyResult(output, records)
