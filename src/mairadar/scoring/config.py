"""Per-feature mapper instances and explicitly versioned provisional parameters."""

from . import DummyPnMapper, FeatureScoreTransformer, ScoreTransformer

# HOLD remains an MVP placeholder. NOTE uses the 2026-09-13 observation batch's
# median and P99 only to make its distribution inspectable; neither is official.
FEATURE_MAPPERS = {
    "hold": DummyPnMapper(p50=1.0, p100=2.0),
    "note": DummyPnMapper(p50=3.540077197, p100=9.328672541),
}


class DefaultScoreTransformer(FeatureScoreTransformer):
    def __init__(self):
        super().__init__(
            FEATURE_MAPPERS,
            mapping_version="provisional-note-p50-p99-20260913-v1",
        )


TRANSFORMER: type[ScoreTransformer] | None = DefaultScoreTransformer
