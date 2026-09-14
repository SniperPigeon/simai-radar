"""Per-feature mapper instances and explicitly versioned provisional parameters."""

from . import DummyPnMapper, FeatureScoreTransformer, IdentityMapper, ScoreTransformer

# JACK and SLIDE_TRICKY are intentionally passed through so the visualizer
# receives their analyzer values without applying stale calibration. NOTE uses
# the 2026-09-13 observation batch's median and P99. PEAK starts with broad
# exploratory anchors. The other SLIDE features use rounded Master/Re:Master
# observation anchors.
# None is official.
FEATURE_MAPPERS = {
    "jack": IdentityMapper(),
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
            mapping_version="provisional-jack-tricky-identity-20260915-v26",
        )


TRANSFORMER: type[ScoreTransformer] | None = DefaultScoreTransformer
