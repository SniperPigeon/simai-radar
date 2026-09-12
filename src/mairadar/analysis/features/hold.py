"""Temporary MVP metric; not a final radar dimension definition."""

from mairadar.analysis.model import AnalysisContext, FeatureResult


class HoldFrequencyAnalyzer:
    def analyze(self, context: AnalysisContext) -> FeatureResult:
        count = sum(event.kind == "hold" for event in context.events)
        duration = context.duration_s
        if duration <= 0:
            return FeatureResult(None, success=False)
        return FeatureResult(count / duration)
