"""Edit this explicit mapping to choose features and their output order."""

from .features import (
    JackSequenceAnalyzer,
    NoteDensityAnalyzer,
    PeakDensityAnalyzer,
    SlideCumulateAnalyzer,
    SlideSequenceAnalyzer,
    SlideTrickyAnalyzer,
)


FEATURES = {
    "jack": JackSequenceAnalyzer,
    "note": NoteDensityAnalyzer,
    "peak": PeakDensityAnalyzer,
    "slide_tricky": SlideTrickyAnalyzer,
    "slide_cumulate": SlideCumulateAnalyzer,
    "slide_sequence": SlideSequenceAnalyzer,
}
