"""Per-feature mapper instances and parameters. All default thresholds are dummy."""

from . import DummyPnMapper, FeatureScoreTransformer, ScoreTransformer

# Raw-value anchors chosen only to exercise the MVP; not official percentiles.
FEATURE_MAPPERS = {
    "hold": DummyPnMapper(p50=1.0, p100=2.0),
}

TRANSFORMER: type[ScoreTransformer] | None = FeatureScoreTransformer
