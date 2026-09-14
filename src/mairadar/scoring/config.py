"""Per-feature mapper instances and explicitly versioned provisional parameters."""

from . import DummyPnMapper, FeatureScoreTransformer, ScoreTransformer

# HOLD remains an MVP placeholder. NOTE uses the 2026-09-13 observation batch's
# median and P99. PEAK starts with broad exploratory anchors. The two SLIDE
# features use rounded Master/Re:Master observation anchors. SLIDE_TRICKY uses
# P99.9 for its upper anchor so the unique Top-5 tail does not saturate at P99.
# None is official.
FEATURE_MAPPERS = {
    "hold": DummyPnMapper(p50=1.0, p100=2.0),
    "note": DummyPnMapper(p50=3.540077197, p100=9.328672541),
    "peak": DummyPnMapper(p50=10.0, p100=20.0),
    "slide_tricky": DummyPnMapper(p50=0.043, p100=0.33),
    "slide_sequence": DummyPnMapper(p50=1.3, p100=2.9),
}


class DefaultScoreTransformer(FeatureScoreTransformer):
    def __init__(self):
        super().__init__(
            FEATURE_MAPPERS,
            mapping_version="provisional-slide-unique-top5-20260914-v20",
        )


TRANSFORMER: type[ScoreTransformer] | None = DefaultScoreTransformer
