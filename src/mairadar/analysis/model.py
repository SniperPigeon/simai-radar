"""In-memory feature contracts, independent of storage and score mapping."""

from dataclasses import dataclass, field
from typing import Protocol

from mairadar.model import Diagnostic, Event
from .control import CancellationCheck, no_cancellation


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
    check_cancelled: CancellationCheck = field(default=no_cancellation, compare=False, repr=False)

    @property
    def duration_s(self) -> float:
        return max(self.chart_end_time_s, self.last_event_end_s or 0.0)


@dataclass(frozen=True)
class FeatureResult:
    data: float | None
    success: bool = True


class FeatureAnalyzer(Protocol):
    def analyze(self, context: AnalysisContext) -> FeatureResult: ...


@dataclass(frozen=True)
class AnalysisResult:
    features: dict[str, FeatureResult]
    parser_diagnostics: tuple[Diagnostic, ...] = ()
    diagnostics: tuple[AnalysisIssue, ...] = ()
    is_cancelled: bool = False

    @property
    def status(self) -> str:
        if self.is_cancelled:
            return "cancelled"
        successful = sum(item.success for item in self.features.values())
        if successful == len(self.features) and not self.diagnostics:
            return "ok"
        return "partial" if successful else "error"
