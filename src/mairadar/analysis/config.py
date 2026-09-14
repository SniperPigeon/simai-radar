"""Edit this explicit mapping to choose features and their output order."""

from .features import (
    HoldFrequencyAnalyzer,
    NoteDensityAnalyzer,
    PeakDensityAnalyzer,
    SlideSequenceAnalyzer,
    SlideTrickyAnalyzer,
)


FEATURES = {
    "hold": HoldFrequencyAnalyzer,
    "note": NoteDensityAnalyzer,
    "peak": PeakDensityAnalyzer,
    "slide_tricky": SlideTrickyAnalyzer,
    "slide_sequence": SlideSequenceAnalyzer,
}
