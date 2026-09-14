"""Per-feature mapper instances and explicitly versioned provisional parameters."""

from . import DummyPnMapper, FeatureScoreTransformer, IdentityMapper, ScoreTransformer

# HOLD remains an MVP placeholder. NOTE uses the 2026-09-13 observation batch's
# median and P99. PEAK starts with broad exploratory anchors. The SLIDE
# features use rounded Master/Re:Master observation anchors. SLIDE_TRICKY is
# intentionally passed through so the visualizer receives the analyzer value
# without applying a stale calibration first.
# None is official.
FEATURE_MAPPERS = {
    "hold": DummyPnMapper(p50=1.0, p100=2.0),
    "note": DummyPnMapper(p50=3.540077197, p100=9.328672541),
    "peak": DummyPnMapper(p50=10.0, p100=20.0),
    "slide_tricky": IdentityMapper(),
    "slide_cumulate": DummyPnMapper(p50=0.36, p100=0.96),
    "slide_sequence": DummyPnMapper(p50=1.3, p100=2.9),
}


class DefaultScoreTransformer(FeatureScoreTransformer):
    def __init__(self):
        super().__init__(
            FEATURE_MAPPERS,
            mapping_version="provisional-tricky-identity-20260915-v25",
        )


TRANSFORMER: type[ScoreTransformer] | None = DefaultScoreTransformer
