"""Load a frozen visualizer calibration for scoring unseen charts."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path

from .transformer import FeatureScoreTransformer


PROFILE_SCHEMA_VERSION = "mairadar-mapping-profile-1"
SCORE_ANCHORS = (50.0, 100.0, 150.0, 200.0)
MAXIMUM_SCORE = 220.0


def _finite_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{field} must be a finite number")
    return float(value)


@dataclass(frozen=True)
class OpenSetPiecewiseMapper:
    """Four linear anchors, a plateau, and an asymptotic open-set tail."""

    raw_anchors: tuple[float, float, float, float]
    t4_max: float
    score_anchors: tuple[float, float, float, float] = SCORE_ANCHORS
    maximum_score: float = MAXIMUM_SCORE

    def __post_init__(self) -> None:
        if not isinstance(self.raw_anchors, tuple) or len(self.raw_anchors) != 4:
            raise ValueError("raw_anchors must contain exactly T1, T2, T3, and T4")
        anchors = tuple(
            _finite_number(value, f"raw_anchors[{index}]")
            for index, value in enumerate(self.raw_anchors)
        )
        if not 0 < anchors[0] < anchors[1] < anchors[2] < anchors[3]:
            raise ValueError("raw anchors must satisfy 0 < T1 < T2 < T3 < T4")
        t4_max = _finite_number(self.t4_max, "t4_max")
        if t4_max < anchors[3]:
            raise ValueError("t4_max must be greater than or equal to T4")
        if not isinstance(self.score_anchors, tuple) or len(self.score_anchors) != 4:
            raise ValueError("score_anchors must contain four values")
        scores = tuple(
            _finite_number(value, f"score_anchors[{index}]")
            for index, value in enumerate(self.score_anchors)
        )
        if not 0 < scores[0] < scores[1] < scores[2] < scores[3]:
            raise ValueError("score anchors must be positive and strictly increasing")
        maximum = _finite_number(self.maximum_score, "maximum_score")
        if maximum <= scores[-1]:
            raise ValueError("maximum_score must exceed the last score anchor")
        object.__setattr__(self, "raw_anchors", anchors)
        object.__setattr__(self, "t4_max", t4_max)
        object.__setattr__(self, "score_anchors", scores)
        object.__setattr__(self, "maximum_score", maximum)

    def map(self, data: float) -> float:
        value = _finite_number(data, "mapping input")
        if value <= 0:
            return 0.0

        lower_raw = 0.0
        lower_score = 0.0
        for upper_raw, upper_score in zip(self.raw_anchors, self.score_anchors, strict=True):
            if value <= upper_raw:
                return lower_score + (
                    (upper_score - lower_score)
                    * (value - lower_raw)
                    / (upper_raw - lower_raw)
                )
            lower_raw = upper_raw
            lower_score = upper_score

        if value <= self.t4_max:
            return self.score_anchors[-1]

        # Start with the T3→T4 slope, then decay toward the configured limit.
        t3, t4 = self.raw_anchors[2:]
        initial_slope = (self.score_anchors[3] - self.score_anchors[2]) / (t4 - t3)
        excess = value - self.t4_max
        headroom = self.maximum_score - self.score_anchors[-1]
        return self.score_anchors[-1] + headroom * (
            1.0 - math.exp(-initial_slope * excess / headroom)
        )


def load_mapping_profile(path: str | Path) -> FeatureScoreTransformer:
    """Validate a mapping_profile.json and construct its score transformer."""

    profile_path = Path(path)
    try:
        payload = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read mapping profile {profile_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Mapping profile root must be an object")
    if payload.get("schemaVersion") != PROFILE_SCHEMA_VERSION:
        raise ValueError(f"Unsupported mapping profile schema: {payload.get('schemaVersion')!r}")
    mapping_version = payload.get("mappingVersion")
    if not isinstance(mapping_version, str) or not mapping_version:
        raise ValueError("Mapping profile needs a nonempty mappingVersion")
    raw_scores = payload.get("scoreAnchors", list(SCORE_ANCHORS))
    if not isinstance(raw_scores, list) or len(raw_scores) != 4:
        raise ValueError("Mapping profile needs four scoreAnchors")
    score_anchors = tuple(raw_scores)
    maximum_score = payload.get("maximumScore", MAXIMUM_SCORE)
    dimensions = payload.get("dimensions")
    if not isinstance(dimensions, dict) or not dimensions:
        raise ValueError("Mapping profile dimensions must be a nonempty object")

    mappers = {}
    for name, config in dimensions.items():
        if not isinstance(name, str) or not name:
            raise ValueError("Mapping profile dimension names must be nonempty strings")
        if not isinstance(config, dict):
            raise ValueError(f"Mapping profile dimension {name} must be an object")
        raw_anchors = config.get("rawAnchors")
        if not isinstance(raw_anchors, list) or len(raw_anchors) != 4:
            raise ValueError(f"Mapping profile dimension {name} needs four rawAnchors")
        try:
            mappers[name] = OpenSetPiecewiseMapper(
                tuple(raw_anchors),
                config.get("t4Max"),
                score_anchors,
                maximum_score,
            )
        except ValueError as exc:
            raise ValueError(f"Invalid mapping profile dimension {name}: {exc}") from exc
    from .linear import IdentityMapper
    mappers.setdefault("fitted_constant", IdentityMapper())
    return FeatureScoreTransformer(mappers, mapping_version=mapping_version)
