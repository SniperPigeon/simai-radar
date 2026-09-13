"""Read each direct child as a bundle; failures remain visible in the report."""

from dataclasses import dataclass
from pathlib import Path

from .analysis import AnalysisIssue, ChartAnalyzer
from .chart_type import detect_chart_type
from .io import find_cover, is_empty_chart, parse_file, read_bundle, read_cover_path
from .model import ChartBundle, ParseResult
from .reporting import AnalysisRecord


UTAGE_DIFFICULTY_INDEX = 7


@dataclass
class BatchResult:
    feature_names: tuple[str, ...]
    records: list[AnalysisRecord]

    @property
    def exit_code(self) -> int:
        return int(not self.records or any(record.status != "ok" for record in self.records))


def _analyze_bundle(bundle: ChartBundle, analyzer: ChartAnalyzer):
    parsed = ParseResult(
        bundle.events, bundle.diagnostics, bundle.complete,
        bundle.chart.chart_end_time_s, bundle.chart.last_event_end_s,
    )
    if bundle.chart.chart_type is None:
        bundle.chart.chart_type = detect_chart_type(parsed)
    return analyzer.analyze(parsed)


def _include_in_distribution(
    index: int | None,
    difficulties: list[int] | None,
    include_utage: bool,
) -> bool:
    if difficulties is None:
        return include_utage or index != UTAGE_DIFFICULTY_INDEX
    return index in difficulties


def analyze_directory(
    root: str | Path, *, analyzer: ChartAnalyzer | None = None, include_cover: bool = True,
    difficulties: list[int] | None = None, include_utage: bool = False,
) -> BatchResult:
    root = Path(root)
    if not root.is_dir():
        raise ValueError(f"Input must be a bundle root directory: {root}")
    if difficulties is not None and any(
        type(index) is not int or index < 1 for index in difficulties
    ):
        raise ValueError("difficulty indexes must be positive integers")
    analyzer = analyzer if analyzer is not None else ChartAnalyzer()
    records = []
    for folder in sorted(root.iterdir()):
        if not folder.is_dir():
            continue
        record = AnalysisRecord(source_name=folder.name)
        records.append(record)
        try:
            bundle = read_bundle(folder)
            if not _include_in_distribution(
                bundle.chart.difficulty_index, difficulties, include_utage,
            ):
                records.pop()
                continue
            if is_empty_chart(bundle):
                records.pop()
                continue
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
    include_utage: bool = False,
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
            if not _include_in_distribution(
                bundle.chart.difficulty_index, difficulties, include_utage,
            ):
                continue
            if is_empty_chart(bundle):
                continue
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
