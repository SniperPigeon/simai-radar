"""Metadata and optional attachments assembled outside the analysis core."""

from dataclasses import dataclass, field
from pathlib import Path

from .analysis.model import AnalysisIssue, AnalysisResult
from .model import Chart
from .scoring import ScoreResult


@dataclass
class AnalysisRecord:
    source_name: str
    chart: Chart | None = None
    analysis: AnalysisResult | None = None
    cover_path: Path | None = None
    scores: ScoreResult | None = None
    diagnostics: list[AnalysisIssue] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.analysis is None:
            return "error"
        if self.diagnostics or (self.scores is not None and any(
            score.status != "ok" for score in self.scores.features.values()
        )):
            return "partial" if self.analysis.status != "error" else "error"
        return self.analysis.status
