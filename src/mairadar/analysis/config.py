"""Edit this explicit mapping to choose features and their output order."""

from .features import (
    HoldFrequencyAnalyzer,
    NoteDensityAnalyzer,
    PeakDensityAnalyzer,
    SlidePressureAnalyzer,
)


FEATURES = {
    "hold": HoldFrequencyAnalyzer,
    "note": NoteDensityAnalyzer,
    "peak": PeakDensityAnalyzer,
    "slide": SlidePressureAnalyzer,
}
