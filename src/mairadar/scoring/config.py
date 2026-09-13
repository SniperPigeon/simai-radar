"""Per-feature mapper instances and explicitly versioned provisional parameters."""

from . import DummyPnMapper, FeatureScoreTransformer, ScoreTransformer

# HOLD remains an MVP placeholder. NOTE uses the 2026-09-13 observation batch's
# median and P99. PEAK starts with broad exploratory anchors. SLIDE uses rounded
# anchors from a limited non-Utage implementation sample. None is official.
FEATURE_MAPPERS = {
    "hold": DummyPnMapper(p50=1.0, p100=2.0),
    "note": DummyPnMapper(p50=3.540077197, p100=9.328672541),
    "peak": DummyPnMapper(p50=10.0, p100=20.0),
    "slide": DummyPnMapper(p50=4.6, p100=27.0),
}


class DefaultScoreTransformer(FeatureScoreTransformer):
    def __init__(self):
        super().__init__(
            FEATURE_MAPPERS,
            mapping_version="provisional-slide-20260913-v8",
        )


TRANSFORMER: type[ScoreTransformer] | None = DefaultScoreTransformer
