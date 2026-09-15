"""Independent feature mapping with a dummy Pn implementation for the MVP."""

from dataclasses import dataclass
from typing import Literal, Protocol

from mairadar.analysis.model import AnalysisIssue, AnalysisResult


@dataclass(frozen=True)
class FeatureScore:
    value: float | None
    status: Literal["ok", "error", "unavailable"] = "ok"
    diagnostics: tuple[AnalysisIssue, ...] = ()


@dataclass(frozen=True)
class ScoreResult:
    features: dict[str, FeatureScore]
    mapping_version: str


class ScoreTransformer(Protocol):
    """Transform raw feature results without changing their values."""

    def transform(self, result: AnalysisResult) -> ScoreResult: ...


class FeatureMapper(Protocol):
    def map(self, data: float) -> float: ...


from .linear import DummyPnMapper, IdentityMapper  # noqa: E402
from .profile import (  # noqa: E402
    MAXIMUM_SCORE,
    PROFILE_SCHEMA_VERSION,
    SCORE_ANCHORS,
    OpenSetPiecewiseMapper,
    load_mapping_profile,
)
from .transformer import FeatureScoreTransformer  # noqa: E402

__all__ = [
    "FeatureMapper", "FeatureScore", "ScoreResult", "ScoreTransformer",
    "DummyPnMapper", "IdentityMapper", "FeatureScoreTransformer",
    "MAXIMUM_SCORE", "PROFILE_SCHEMA_VERSION", "SCORE_ANCHORS",
    "OpenSetPiecewiseMapper", "load_mapping_profile",
]
