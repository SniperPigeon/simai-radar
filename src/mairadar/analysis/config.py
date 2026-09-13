"""Edit this explicit mapping to choose features and their output order."""

from .features import HoldFrequencyAnalyzer, NoteDensityAnalyzer


FEATURES = {
    "hold": HoldFrequencyAnalyzer,
    "note": NoteDensityAnalyzer,
}
