"""Per-feature mapper instances and explicitly versioned provisional parameters."""

from . import DummyPnMapper, FeatureScoreTransformer, IdentityMapper, ScoreTransformer

# JACK, SWEEP and SLIDE_TRICKY are intentionally passed through so the visualizer
# receives their analyzer values without applying stale calibration. NOTE uses
# the 2026-09-13 observation batch's median and P99. PEAK starts with broad
# exploratory anchors. SLIDE_SEQUENCE uses rounded Master/Re:Master observation
# anchors. SLIDE_CUMULATE uses provisional standalone anchors unless a frozen
# mapping profile overrides them. All parameters below are exploratory.
# None is official.
FEATURE_MAPPERS = {
    "note": DummyPnMapper(p50=3.540077197, p100=9.328672541),
    "peak": DummyPnMapper(p50=10.0, p100=20.0),
    "sweep": IdentityMapper(),
    "slide_tricky": IdentityMapper(),
    "slide_sequence": DummyPnMapper(p50=1.3, p100=2.9),
    "jack": IdentityMapper(),
    "slide_cumulate": DummyPnMapper(p50=0.36, p100=0.96),
    "fitted_constant": IdentityMapper(),
}


class DefaultScoreTransformer(FeatureScoreTransformer):
    def __init__(self):
        super().__init__(
            FEATURE_MAPPERS,
            mapping_version="provisional-sweep-2s-top3-cumulate-star8-top5-20260916-v51",
        )


TRANSFORMER: type[ScoreTransformer] | None = DefaultScoreTransformer
