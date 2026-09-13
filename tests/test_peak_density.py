"""Weighted local burst density over corrected Note workloads."""

import unittest
import math

from mairadar.analysis import AnalysisContext
from mairadar.analysis.features import PeakDensityAnalyzer
from mairadar.analysis.features.peak import _peak_density, _peak_workload_points
from mairadar.parser import parse_chart


class PeakDensityTests(unittest.TestCase):
    def test_peak_uses_flat_slide_and_half_touch_group_weights(self):
        parsed = parse_chart("(120){4}1?<1<1[4:2]/A1/E1,E")
        points = _peak_workload_points(tuple(parsed.events))
        self.assertEqual(sorted(weight for _, weight in points), [0.5, 1.0])

    def test_equal_interval_adjacent_sweep_decays_from_the_fifth_button(self):
        parsed = parse_chart("(120){16}1,2,3,4,5,6,7,E")
        points = _peak_workload_points(tuple(parsed.events))
        weights = [weight for _, weight in sorted(points)]
        self.assertEqual(weights[:4], [1.0] * 4)
        self.assertAlmostEqual(weights[4], 1 / math.log2(3))
        self.assertEqual(weights[5], 0.5)
        self.assertAlmostEqual(weights[6], 1 / math.log2(5))

    def test_direction_change_can_start_a_new_sweep_at_the_previous_button(self):
        parsed = parse_chart("(120){16}1,2,3,4,5,4,3,2,1,E")
        points = _peak_workload_points(tuple(parsed.events))
        weights = [weight for _, weight in sorted(points)]
        self.assertAlmostEqual(weights[4], 1 / math.log2(3))
        self.assertEqual(weights[5:8], [1.0] * 3)
        self.assertAlmostEqual(weights[8], 1 / math.log2(3))

    def test_isolated_peak_keeps_only_the_center_weight(self):
        # Workload 6 / 1.5s = density 4; empty shoulders leave 0.6 * 4.
        # As the sole nonzero peak it then receives the first-peak weight 0.5.
        self.assertEqual(_peak_density([(0.0, 6)], 4.5), 1.2)

    def test_sustained_three_window_burst_reaches_its_full_density(self):
        points = [(0.0, 6), (1.5, 6), (3.0, 6)]
        self.assertEqual(_peak_density(points, 4.5), 2.0)

    def test_maximizes_weighted_neighborhood_instead_of_locking_to_top1(self):
        points = [
            (0.0, 6),  # isolated center density 4 -> weighted score 2.4
            (5.0, 5), (6.5, 5), (8.0, 5),  # sustained density 10/3
        ]
        self.assertAlmostEqual(_peak_density(points, 9.5), (0.5 * 10 / 3) + (0.3 * 2.4))

    def test_three_non_overlapping_peaks_use_5_3_2_weights(self):
        # Isolated workloads 10, 7.5 and 5 produce local scores 4, 3 and 2.
        points = [(0.0, 10), (10.0, 7.5), (20.0, 5)]
        self.assertEqual(_peak_density(points, 25.0), 3.3)

    def test_overlapping_candidates_do_not_fill_multiple_peak_slots(self):
        # Many 0.5-second candidate windows see this one burst, but its 4.5-second
        # neighborhood can be selected only once.
        self.assertEqual(_peak_density([(5.0, 10)], 12.0), 2.0)

    def test_half_second_stride_can_join_a_fixed_window_boundary_burst(self):
        # The 0.5-starting center window contains both sides of the 1.5 boundary.
        score = _peak_density([(1.49, 1), (1.51, 1)], 3.0)
        self.assertEqual(score, 0.5 * 0.6 * (2 / 1.5))

    def test_positive_empty_chart_is_zero_and_zero_duration_is_unavailable(self):
        analyzer = PeakDensityAnalyzer()
        empty = AnalysisContext((), chart_end_time_s=1.0, last_event_end_s=None)
        zeroth = AnalysisContext((), chart_end_time_s=0.0, last_event_end_s=None)
        self.assertEqual(analyzer.analyze(empty).data, 0.0)
        self.assertFalse(analyzer.analyze(zeroth).success)


if __name__ == "__main__":
    unittest.main()
