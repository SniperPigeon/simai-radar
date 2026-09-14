"""Edit this explicit mapping to choose features and their output order."""

from .features import (
    HoldFrequencyAnalyzer,
    NoteDensityAnalyzer,
    PeakDensityAnalyzer,
    SlideCumulateAnalyzer,
    SlideSequenceAnalyzer,
    SlideTrickyAnalyzer,
)


FEATURES = {
    "hold": HoldFrequencyAnalyzer,
    "note": NoteDensityAnalyzer,
    "peak": PeakDensityAnalyzer,
    "slide_tricky": SlideTrickyAnalyzer,
    "slide_cumulate": SlideCumulateAnalyzer,
    "slide_sequence": SlideSequenceAnalyzer,
}
