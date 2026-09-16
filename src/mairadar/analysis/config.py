"""Edit this explicit mapping to choose features and their output order."""

from .features import (
    JackSequenceAnalyzer,
    NoteDensityAnalyzer,
    PeakDensityAnalyzer,
    # SlideCumulateAnalyzer,
    SlideSequenceAnalyzer,
    SlideTrickyAnalyzer,
    SweepBurstAnalyzer,
)


FEATURES = {
    "note": NoteDensityAnalyzer,
    "peak": PeakDensityAnalyzer,
    "sweep": SweepBurstAnalyzer,
    "slide_tricky": SlideTrickyAnalyzer,
    "slide_sequence": SlideSequenceAnalyzer,
    "jack": JackSequenceAnalyzer,
    # "slide_cumulate": SlideCumulateAnalyzer,
}
