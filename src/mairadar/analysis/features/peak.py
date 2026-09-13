"""Local burst density over a weighted three-window neighborhood."""

from bisect import bisect_left
import math

from mairadar.analysis.model import AnalysisContext, FeatureResult

from .note_density import WINDOW_SECONDS, corrected_workload_points


PEAK_STEP_SECONDS = 0.5
LEFT_WEIGHT = 0.2
CENTER_WEIGHT = 0.6
RIGHT_WEIGHT = 0.2


def _peak_density(points: list[tuple[float, int]], duration_s: float) -> float:
    if not points:
        return 0.0
    points = sorted(points)
    times = [time_s for time_s, _ in points]
    prefix = [0]
    for _, weight in points:
        prefix.append(prefix[-1] + weight)

    def density(start_s: float) -> float:
        end_s = start_s + WINDOW_SECONDS
        begin = bisect_left(times, start_s)
        end = bisect_left(times, end_s)
        return (prefix[end] - prefix[begin]) / WINDOW_SECONDS

    ratio = duration_s / PEAK_STEP_SECONDS
    nearest = round(ratio)
    if math.isclose(ratio, nearest, rel_tol=1e-12, abs_tol=1e-12):
        ratio = float(nearest)
    candidate_count = math.floor(ratio) + 1

    peak = 0.0
    for index in range(candidate_count):
        center_start = index * PEAK_STEP_SECONDS
        score = (
            LEFT_WEIGHT * density(center_start - WINDOW_SECONDS)
            + CENTER_WEIGHT * density(center_start)
            + RIGHT_WEIGHT * density(center_start + WINDOW_SECONDS)
        )
        if score > peak:
            peak = score
    return peak


class PeakDensityAnalyzer:
    """Find the strongest 4.5-second weighted burst neighborhood."""

    def analyze(self, context: AnalysisContext) -> FeatureResult:
        duration = context.duration_s
        if duration <= 0:
            return FeatureResult(None, success=False)
        points = corrected_workload_points(context.events)
        return FeatureResult(_peak_density(points, duration))
