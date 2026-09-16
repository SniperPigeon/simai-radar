"""Experimental fixed-window sweep burst scoring."""

import math
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
            same_direction_handoff_bonus=0,
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

    def test_long_simple_run_decays_only_after_sixteen_attacks(self):
        body = ",".join("12345678" * 3)
        parsed = parse_chart(f"(180){{16}}{body},E")
        result = score_sweep_burst(
            tuple(parsed.events),
            duration_s=parsed.chart_end_time_s,
            idle_distance_weight=0,
            takeover_weight=0,
            fast_jump_weight=0,
            same_direction_connection_bonus=0,
            same_direction_handoff_bonus=0,
        )
        expected = 16 + sum(1 / math.sqrt(rank) for rank in range(2, 10))
        self.assertAlmostEqual(result.base_density, expected / 3)

    def test_double_sweep_disables_simple_run_decay(self):
        body = ",".join(("15", "26", "37", "48") * 6)
        parsed = parse_chart(f"(180){{16}}{body},E")
        result = score_sweep_burst(
            tuple(parsed.events),
            duration_s=parsed.chart_end_time_s,
            idle_distance_weight=0,
            takeover_weight=0,
            fast_jump_weight=0,
            same_direction_connection_bonus=0,
            same_direction_handoff_bonus=0,
        )
        self.assertAlmostEqual(result.base_density, 48 / 3)

    def test_same_direction_chord_handoff_gets_local_bonus(self):
        parsed = parse_chart("(180){16}3,4,56,7,8,E")
        result = score_sweep_burst(
            tuple(parsed.events),
            duration_s=parsed.chart_end_time_s,
            idle_distance_weight=0,
            takeover_weight=0,
            fast_jump_weight=0,
            same_direction_connection_bonus=0,
        )
        self.assertAlmostEqual(result.base_density, (6 + 2 * 0.2) / 3)

    def test_same_direction_family_connection_gets_local_bonus(self):
        parsed = parse_chart("(180){16}1,2,3,8,1,2,E")
        result = score_sweep_burst(
            tuple(parsed.events),
            duration_s=parsed.chart_end_time_s,
            idle_distance_weight=0,
            takeover_weight=0,
            fast_jump_weight=0,
            same_direction_handoff_bonus=0,
        )
        self.assertAlmostEqual(result.base_density, (6 + 0.2) / 3)


if __name__ == "__main__":
    unittest.main()
