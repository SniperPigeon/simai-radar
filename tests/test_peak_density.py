"""Weighted local burst density over corrected Note workloads."""

import unittest

from mairadar.analysis import AnalysisContext
from mairadar.analysis.features import PeakDensityAnalyzer
from mairadar.analysis.features.peak import _peak_density


class PeakDensityTests(unittest.TestCase):
    def test_isolated_peak_keeps_only_the_center_weight(self):
        # Workload 6 / 1.5s = density 4; empty shoulders leave 0.6 * 4.
        self.assertEqual(_peak_density([(0.0, 6)], 4.5), 2.4)

    def test_sustained_three_window_burst_reaches_its_full_density(self):
        points = [(0.0, 6), (1.5, 6), (3.0, 6)]
        self.assertEqual(_peak_density(points, 4.5), 4.0)

    def test_maximizes_weighted_neighborhood_instead_of_locking_to_top1(self):
        points = [
            (0.0, 6),  # isolated center density 4 -> weighted score 2.4
            (5.0, 5), (6.5, 5), (8.0, 5),  # sustained density 10/3
        ]
        self.assertAlmostEqual(_peak_density(points, 9.5), 10 / 3)

    def test_half_second_stride_can_join_a_fixed_window_boundary_burst(self):
        # The 0.5-starting center window contains both sides of the 1.5 boundary.
        score = _peak_density([(1.49, 1), (1.51, 1)], 3.0)
        self.assertEqual(score, 0.6 * (2 / 1.5))

    def test_positive_empty_chart_is_zero_and_zero_duration_is_unavailable(self):
        analyzer = PeakDensityAnalyzer()
        empty = AnalysisContext((), chart_end_time_s=1.0, last_event_end_s=None)
        zeroth = AnalysisContext((), chart_end_time_s=0.0, last_event_end_s=None)
        self.assertEqual(analyzer.analyze(empty).data, 0.0)
        self.assertFalse(analyzer.analyze(zeroth).success)


if __name__ == "__main__":
    unittest.main()
