"""Experimental fixed-window sweep burst scoring."""

import unittest

from mairadar.analysis.features.sweep_burst import score_sweep_burst
from mairadar.parser import parse_chart


class SweepBurstTests(unittest.TestCase):
    def test_three_second_window_zero_pads_short_handoff_sweep(self):
        parsed = parse_chart("(180){16}3,4,56,7,8,E")
        result = score_sweep_burst(
            tuple(parsed.events),
            duration_s=parsed.chart_end_time_s,
            idle_distance_weight=0,
            takeover_weight=0,
            fast_jump_weight=0,
        )
        self.assertAlmostEqual(result.base_density, 6 / 3)
        self.assertEqual(result.motion_density, 0)

    def test_plain_alternation_stays_outside_sweep_burst(self):
        parsed = parse_chart("(180){16}2,3,2,3,E")
        result = score_sweep_burst(
            tuple(parsed.events),
            duration_s=parsed.chart_end_time_s,
        )
        self.assertEqual(result.value, 0)


if __name__ == "__main__":
    unittest.main()
