"""Dispatch each raw feature to its own configured mapper instance."""

from collections.abc import Mapping
import math

from mairadar.analysis import AnalysisIssue, AnalysisResult
from . import FeatureMapper, FeatureScore, ScoreResult


class FeatureScoreTransformer:
    def __init__(
        self, mappers: Mapping[str, FeatureMapper] | None = None,
        *, mapping_version: str = "dummy-pn-v1",
    ):
        if mappers is None:
            from .config import FEATURE_MAPPERS
            mappers = FEATURE_MAPPERS
        self._mappers = dict(mappers)
        if not isinstance(mapping_version, str) or not mapping_version.strip():
            raise ValueError("mapping_version must be a nonempty string")
        self.mapping_version = mapping_version

    def transform(self, result: AnalysisResult) -> ScoreResult:
        scores = {}
        for name, feature in result.features.items():
            if not feature.success:
                scores[name] = FeatureScore(None, "unavailable")
                continue
            if name not in self._mappers:
                scores[name] = FeatureScore(None, "error", (
                    AnalysisIssue("MAPPER_MISSING", "No mapper configured for this feature", name),
                ))
                continue
            try:
                value = self._mappers[name].map(feature.data)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError("Mapper must return a finite numeric score")
                scores[name] = FeatureScore(value)
            except Exception as exc:
                scores[name] = FeatureScore(None, "error", (
                    AnalysisIssue("MAPPING_FAILED", str(exc), name),
                ))
        return ScoreResult(scores, self.mapping_version)
