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
            window_seconds=3,
            window_count=1,
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
            window_seconds=3,
            window_count=1,
            idle_distance_weight=0,
            takeover_weight=0,
            fast_jump_weight=0,
            same_direction_connection_bonus=0,
            same_direction_handoff_bonus=0,
        )
        expected = 16 + sum(1 / math.sqrt(rank) for rank in range(2, 10))
        self.assertAlmostEqual(result.base_density, expected / 3)

    def test_double_sweep_batches_remain_full_weight(self):
        body = ",".join(("15", "26", "37", "48") * 6)
        parsed = parse_chart(f"(180){{16}}{body},E")
        result = score_sweep_burst(
            tuple(parsed.events),
            duration_s=parsed.chart_end_time_s,
            window_seconds=3,
            window_count=1,
            idle_distance_weight=0,
            takeover_weight=0,
            fast_jump_weight=0,
            same_direction_connection_bonus=0,
            same_direction_handoff_bonus=0,
        )
        self.assertAlmostEqual(result.base_density, 48 / 3)

    def test_double_batch_resets_single_run_decay_counter(self):
        before = list("12345678123456781")
        after = list("34567812345678123")
        body = ",".join((*before, "2/6", *after))
        parsed = parse_chart(f"(180){{16}}{body},E")
        result = score_sweep_burst(
            tuple(parsed.events),
            duration_s=parsed.chart_end_time_s,
            window_seconds=3,
            window_count=1,
            idle_distance_weight=0,
            takeover_weight=0,
            fast_jump_weight=0,
            same_direction_connection_bonus=0,
            same_direction_handoff_bonus=0,
            pattern_motion_floor=1,
        )
        expected = 2 * (16 + 1 / math.sqrt(2)) + 2
        self.assertAlmostEqual(result.base_density, expected / 3)

    def test_same_direction_chord_handoff_gets_local_bonus(self):
        parsed = parse_chart("(180){16}3,4,56,7,8,E")
        result = score_sweep_burst(
            tuple(parsed.events),
            duration_s=parsed.chart_end_time_s,
            window_seconds=3,
            window_count=1,
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
            window_seconds=3,
            window_count=1,
            idle_distance_weight=0,
            takeover_weight=0,
            fast_jump_weight=0,
            same_direction_handoff_bonus=0,
        )
        self.assertAlmostEqual(result.base_density, (6 + 0.2) / 3)

    def test_periodic_group_handoffs_discount_only_motion(self):
        body = ",".join(("1,2,3,4", "8,7,6,5") * 4)
        parsed = parse_chart(f"(180){{16}}{body},E")
        result = score_sweep_burst(
            tuple(parsed.events),
            duration_s=parsed.chart_end_time_s,
            window_seconds=3,
            window_count=1,
            simple_run_full_attacks=100,
            same_direction_connection_bonus=0,
            same_direction_handoff_bonus=0,
        )
        self.assertGreater(result.raw_motion_density, 0)
        self.assertAlmostEqual(
            result.motion_density,
            result.raw_motion_density * 0.1,
        )

    def test_pattern_mismatch_keeps_reverse_hand_motion(self):
        body = ",".join((
            "8,7,6,5",
            "8,1,2,3",
            "7,6,5",
            "2,3,4",
            "8,7,6",
            "1,2,3",
            "7,6,5",
            "8,7,6",
            "2,3,4",
            "1,2,3",
            "8,7,6,5",
            "1,2,3,4",
        ))
        parsed = parse_chart(f"(180){{16}}{body},E")
        result = score_sweep_burst(
            tuple(parsed.events),
            duration_s=parsed.chart_end_time_s,
            window_seconds=3,
            window_count=1,
            simple_run_full_attacks=100,
            same_direction_connection_bonus=0,
            same_direction_handoff_bonus=0,
        )
        self.assertGreater(result.motion_density, result.raw_motion_density * 0.1)
        self.assertLess(result.motion_density, result.raw_motion_density)

    def test_same_direction_connections_are_protected_from_pattern_discount(self):
        body = ",".join((
            "1,2,3",
            "8,1,2",
            "7,8,1",
            "6,7,8",
            "5,6,7",
            "4,5,6",
        ))
        parsed = parse_chart(f"(180){{16}}{body},E")
        result = score_sweep_burst(
            tuple(parsed.events),
            duration_s=parsed.chart_end_time_s,
            window_seconds=3,
            window_count=1,
            simple_run_full_attacks=100,
            same_direction_handoff_bonus=0,
        )
        self.assertGreater(result.raw_motion_density, 0)
        self.assertEqual(result.motion_density, result.raw_motion_density)

    def test_top_three_two_second_windows_are_non_overlapping_and_weighted(self):
        gap = "," * 30
        body = gap.join(("1,2,3,4", "1,2,3,4", "1,2,3,4")) + gap
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
        self.assertEqual(len(result.windows), 3)
        for left, right in zip(
            sorted(result.windows, key=lambda item: item.window_start_s),
            sorted(result.windows, key=lambda item: item.window_start_s)[1:],
        ):
            self.assertLessEqual(left.window_end_s, right.window_start_s + 1e-9)
        weights = (1.0, 1 / math.sqrt(2), 1 / math.sqrt(3))
        expected = sum(
            weight * window.value
            for weight, window in zip(weights, result.windows)
        ) / sum(weights)
        self.assertAlmostEqual(result.value, expected)


if __name__ == "__main__":
    unittest.main()
