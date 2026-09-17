"""Compose input adapters, analysis, optional mapping, and an injected exporter."""

from copy import deepcopy
import math
from pathlib import Path
from typing import Protocol

from .analysis import AnalysisIssue, ChartAnalyzer, flatten_features
from .batch import BatchResult, analyze_directory, analyze_source
from .parse_export import ParseOnlyResult, parse_source_to_bundles
from .scoring import FeatureScore, ScoreResult, ScoreTransformer

class ExportReport(Protocol):
    failed_records: int

    @property
    def exit_code(self) -> int: ...


class ReportExporter(Protocol):
    def export(self, records, output, *, feature_names, include_scores) -> ExportReport: ...

MODES = ("full", "parse_only", "analysis", "analysis_score")
RAW_MODES = {"full", "parse_only"}
SCORING_MODES = {"full", "analysis_score"}


def _map_batch(batch: BatchResult, transformer: ScoreTransformer) -> None:
    for record in batch.records:
        if record.analysis is None or not any(item.success for item in record.analysis.features.values()):
            continue
        try:
            scores = transformer.transform(deepcopy(record.analysis))
            raw = flatten_features(record.analysis)
            if not isinstance(scores, ScoreResult) or not set(scores.features) <= set(raw):
                raise ValueError("Mapper must return scores for available raw features")
            if not isinstance(scores.mapping_version, str) or not scores.mapping_version.strip():
                raise ValueError("Mapper must identify its mapping_version")
            for name, score in scores.features.items():
                if not isinstance(score, FeatureScore) or score.status not in {"ok", "error", "unavailable"}:
                    raise ValueError(f"Invalid score result: {name}")
                if score.status == "ok":
                    if not raw[name].success:
                        raise ValueError(f"Cannot score an unsuccessful feature: {name}")
                    if (isinstance(score.value, bool) or not isinstance(score.value, (float, int))
                            or not math.isfinite(score.value)):
                        raise ValueError(f"Score must be finite: {name}")
                elif score.value is not None:
                    raise ValueError(f"Unsuccessful score must be null: {name}")
            record.scores = scores
        except Exception as exc:
            record.diagnostics.append(AnalysisIssue("MAPPING_FAILED", str(exc)))


def run_pipeline(
    mode: str, source: str | Path, *, output: str | Path | None = None,
    analyzer: ChartAnalyzer | None = None, transformer: ScoreTransformer | None = None,
    exporter: ReportExporter | None = None,
    difficulties: list[int] | None = None, chart_type: str | None = None,
    include_utage: bool = False,
    constant_model=None,
) -> tuple[BatchResult | ParseOnlyResult, ExportReport | None]:
    """analysis returns raw results only; scoring modes require an explicit mapper."""
    if mode not in MODES:
        raise ValueError(f"Unknown mode: {mode}")
    if mode == "parse_only" and constant_model is not None:
        raise ValueError("Constant prediction requires analysis")
    if mode not in RAW_MODES and chart_type is not None:
        raise ValueError("Chart type override is only supported in raw-input modes")
    if mode == "analysis":
        if output is not None:
            raise ValueError("analysis does not export; omit --output")
    else:
        if output is None:
            raise ValueError(f"{mode} requires --output")
        if mode in SCORING_MODES and transformer is None:
            raise ValueError(
                "No score transformer configured. Set TRANSFORMER in mairadar/scoring/config.py "
                "or inject a transformer. Use analysis for raw features."
            )
        source_path, output_path = Path(source).resolve(), Path(output).resolve()
        if source_path.is_relative_to(output_path) or output_path.is_relative_to(source_path):
            raise ValueError("Input and output paths must not contain one another")
        target = Path(output)
        if target.is_symlink() or (target.exists() and (
            not target.is_dir() or any(target.iterdir())
        )):
            raise FileExistsError(f"Output must be a new or empty directory: {target}")

    if mode == "parse_only":
        parsed = parse_source_to_bundles(
            source,
            output,
            difficulties=difficulties,
            chart_type=chart_type,
        )
        if not parsed.records:
            raise ValueError("No raw chart files found")
        return parsed, None

    analyzer = analyzer if analyzer is not None else ChartAnalyzer()
    if mode == "full":
        batch = analyze_source(
            source,
            analyzer=analyzer,
            difficulties=difficulties,
            chart_type=chart_type,
            include_utage=include_utage,
        )
    else:
        batch = analyze_directory(
            source,
            analyzer=analyzer,
            include_cover=mode != "analysis",
            difficulties=difficulties,
            include_utage=include_utage,
        )
    if not batch.records:
        raise ValueError("No raw chart files or child bundle directories found")
    if constant_model is not None:
        from .regression.derived import with_prediction
        if "fitted_constant" in batch.feature_names:
            raise ValueError("fitted_constant is reserved for model output")
        for record in batch.records:
            if record.analysis is not None:
                record.analysis = with_prediction(record.analysis, constant_model)
        batch.feature_names += ("fitted_constant",)
    if mode == "analysis":
        return batch, None
    _map_batch(batch, transformer)
    if exporter is None:
        from .exporters import CsvExporter
        exporter = CsvExporter()
    report = exporter.export(batch.records, output, feature_names=batch.feature_names, include_scores=True)
    return batch, report
