"""Edit this explicit mapping to choose features and their output order."""

from .features import HoldFrequencyAnalyzer, NoteDensityAnalyzer, PeakDensityAnalyzer


FEATURES = {
    "hold": HoldFrequencyAnalyzer,
    "note": NoteDensityAnalyzer,
    "peak": PeakDensityAnalyzer,
}
