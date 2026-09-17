"""In-memory feature contracts, independent of storage and score mapping."""

from dataclasses import dataclass, field
import math
from typing import Protocol

from mairadar.model import Diagnostic, Event


@dataclass(frozen=True)
class AnalysisIssue:
    code: str
    message: str
    feature: str | None = None


@dataclass(frozen=True)
class AnalysisContext:
    """A chart snapshot. Feature implementations must treat its events as read-only."""

    events: tuple[Event, ...]
    chart_end_time_s: float
    last_event_end_s: float | None

    @property
    def duration_s(self) -> float:
        return max(self.chart_end_time_s, self.last_event_end_s or 0.0)


@dataclass(frozen=True)
class FeatureResult:
    data: float | None
    success: bool = True
    stats: dict[str, float | None] = field(default_factory=dict)


class FeatureAnalyzer(Protocol):
    def analyze(self, context: AnalysisContext) -> FeatureResult: ...


@dataclass(frozen=True)
class AnalysisResult:
    features: dict[str, FeatureResult]
    parser_diagnostics: tuple[Diagnostic, ...] = ()
    diagnostics: tuple[AnalysisIssue, ...] = ()

    @property
    def status(self) -> str:
        successful = sum(item.success for item in self.features.values())
        if successful == len(self.features) and not self.diagnostics:
            return "ok"
        return "partial" if successful else "error"


def flatten_features(result: AnalysisResult) -> dict[str, FeatureResult]:
    """One scalar view for scoring, model inputs and export; retain original names."""
    values = {name: FeatureResult(item.data, item.success) for name, item in result.features.items()}
    for name, item in result.features.items():
        for stat, value in item.stats.items():
            key = f"{name}_{stat}"
            if key in values:
                raise ValueError(f"Conflicting feature name: {key}")
            valid = item.success and value is not None and not isinstance(value, bool) and math.isfinite(value)
            values[key] = FeatureResult(value if valid else None, valid)
    return values
