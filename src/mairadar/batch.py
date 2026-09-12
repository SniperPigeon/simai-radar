"""Read each direct child as a bundle; failures remain visible in the report."""

from dataclasses import dataclass
from pathlib import Path

from .analysis import AnalysisIssue, ChartAnalyzer
from .io import find_cover, parse_file, read_bundle, read_cover_path
from .model import ChartBundle, ParseResult
from .reporting import AnalysisRecord


@dataclass
class BatchResult:
    feature_names: tuple[str, ...]
    records: list[AnalysisRecord]

    @property
    def exit_code(self) -> int:
        return int(not self.records or any(record.status != "ok" for record in self.records))


def _analyze_bundle(bundle: ChartBundle, analyzer: ChartAnalyzer):
    return analyzer.analyze(ParseResult(
        bundle.events, bundle.diagnostics, bundle.complete,
        bundle.chart.chart_end_time_s, bundle.chart.last_event_end_s,
    ))


def analyze_directory(
    root: str | Path, *, analyzer: ChartAnalyzer | None = None, include_cover: bool = True,
) -> BatchResult:
    root = Path(root)
    if not root.is_dir():
        raise ValueError(f"Input must be a bundle root directory: {root}")
    analyzer = analyzer if analyzer is not None else ChartAnalyzer()
    records = []
    for folder in sorted(root.iterdir()):
        if not folder.is_dir():
            continue
        record = AnalysisRecord(source_name=folder.name)
        records.append(record)
        try:
            bundle = read_bundle(folder)
            record.chart = bundle.chart
            record.analysis = _analyze_bundle(bundle, analyzer)
            if include_cover:
                record.cover_path = read_cover_path(folder)
        except Exception as exc:
            record.diagnostics.append(AnalysisIssue("BUNDLE_FAILED", str(exc)))
    return BatchResult(analyzer.feature_names, records)


def analyze_source(
    source: str | Path, *, analyzer: ChartAnalyzer | None = None,
    difficulties: list[int] | None = None, chart_type: str | None = None,
) -> BatchResult:
    """Parse raw files once and analyze in memory, without intermediate bundles."""
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
    analyzer = analyzer if analyzer is not None else ChartAnalyzer()
    records = []
    for path in files:
        name = path.relative_to(root).as_posix()
        try:
            bundles = parse_file(path, source_name=name, difficulties=difficulties)
        except Exception as exc:
            records.append(AnalysisRecord(
                name, diagnostics=[AnalysisIssue("PARSE_FAILED", str(exc))],
            ))
            continue
        for bundle in bundles:
            record = AnalysisRecord(name, chart=bundle.chart)
            records.append(record)
            try:
                if chart_type is not None:
                    bundle.chart.chart_type = chart_type
                record.analysis = _analyze_bundle(bundle, analyzer)
                record.cover_path = find_cover(path.parent)
            except Exception as exc:
                record.diagnostics.append(AnalysisIssue("ANALYSIS_FAILED", str(exc)))
    return BatchResult(analyzer.feature_names, records)
