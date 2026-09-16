"""Experimental fixed-window sweep burst scoring."""

import math
import unittest

from mairadar.analysis.features.sweep_burst import (
    _idle_distance_pressure,
    score_sweep_burst,
)
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

    def test_ex_discount_does_not_reduce_same_direction_connection_bonus(self):
        def calculate(body, connection_bonus):
            parsed = parse_chart(f"(180){{16}}{body},E")
            return score_sweep_burst(
                tuple(parsed.events),
                duration_s=parsed.chart_end_time_s,
                window_seconds=3,
                window_count=1,
                idle_distance_weight=0,
                takeover_weight=0,
                fast_jump_weight=0,
                same_direction_connection_bonus=connection_bonus,
                same_direction_handoff_bonus=0,
            ).base_density

        normal_bonus = calculate("1,2,3,8,1,2", 0.2) - calculate(
            "1,2,3,8,1,2", 0,
        )
        ex_bonus = calculate("1,2,3,8x,1,2", 0.2) - calculate(
            "1,2,3,8x,1,2", 0,
        )
        self.assertAlmostEqual(normal_bonus, 0.2 / 3)
        self.assertAlmostEqual(ex_bonus, normal_bonus)
        self.assertAlmostEqual(
            calculate("1,2,3,8,1,2", 0.2)
            - calculate("1,2,3,8x,1,2", 0.2),
            0.7 / 3,
        )

    def test_ex_discount_does_not_reduce_double_handoff_bonus(self):
        def calculate(body, handoff_bonus):
            parsed = parse_chart(f"(180){{16}}{body},E")
            return score_sweep_burst(
                tuple(parsed.events),
                duration_s=parsed.chart_end_time_s,
                window_seconds=3,
                window_count=1,
                idle_distance_weight=0,
                takeover_weight=0,
                fast_jump_weight=0,
                same_direction_connection_bonus=0,
                same_direction_handoff_bonus=handoff_bonus,
            ).base_density

        normal_bonus = calculate("3,4,56,7,8", 0.2) - calculate(
            "3,4,56,7,8", 0,
        )
        ex_bonus = calculate("3,4,5x/6,7,8", 0.2) - calculate(
            "3,4,5x/6,7,8", 0,
        )
        self.assertAlmostEqual(normal_bonus, 0.4 / 3)
        self.assertAlmostEqual(ex_bonus, normal_bonus)

    def test_idle_speed_pressure_uses_each_hands_available_time(self):
        self.assertAlmostEqual(
            _idle_distance_pressure(4, 0.25, 10),
            4 * math.sqrt(8 / 5),
        )
        self.assertEqual(_idle_distance_pressure(2, 0.2, 10), 2)
        self.assertEqual(_idle_distance_pressure(3, 5 / 12, 10), 3)
        self.assertEqual(_idle_distance_pressure(0, 0, 10), 0)

    def test_fast_idle_reposition_raises_motion_but_not_base(self):
        body = "2x,3,4,1x,2,3,8x,1,2,7x,8,1,6x,7,8,5x,6,7,4x,5,6,3x,4,5"
        parsed = parse_chart(f"(160){{24}}{body},E")
        common = {
            "duration_s": parsed.chart_end_time_s,
            "window_count": 1,
        }
        pressured = score_sweep_burst(
            tuple(parsed.events),
            **common,
        )
        unpressured = score_sweep_burst(
            tuple(parsed.events),
            idle_speed_reference_keys_per_second=1e9,
            **common,
        )
        self.assertAlmostEqual(pressured.base_density, unpressured.base_density)
        self.assertGreater(pressured.motion_density, unpressured.motion_density)
        self.assertAlmostEqual(
            pressured.motion_density - unpressured.motion_density,
            6 * 4 * 0.5 * (math.sqrt(8 / 5) - 1) / 2,
        )

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

    def test_alternating_template_reduces_idle_but_preserves_takeovers(self):
        body = ",".join(("1,2,3,4", "8,7,6,5") * 4)
        parsed = parse_chart(f"(180){{16}}{body},E")
        common = {
            "duration_s": parsed.chart_end_time_s,
            "window_seconds": 3,
            "window_count": 1,
            "simple_run_decay_exponent": 0,
            "same_direction_connection_bonus": 0,
            "same_direction_handoff_bonus": 0,
        }

        def motion(idle_multiplier, takeover_weight):
            return score_sweep_burst(
                tuple(parsed.events),
                alternating_idle_multiplier=idle_multiplier,
                takeover_weight=takeover_weight,
                **common,
            )

        full = motion(1.0, 1.0)
        reduced = motion(0.2, 1.0)
        full_without_takeovers = motion(1.0, 0.0)
        reduced_without_takeovers = motion(0.2, 0.0)
        self.assertAlmostEqual(full.base_density, reduced.base_density)
        self.assertLess(reduced.motion_density, full.motion_density)
        self.assertAlmostEqual(
            full.motion_density - full_without_takeovers.motion_density,
            reduced.motion_density - reduced_without_takeovers.motion_density,
        )

    def test_solo_fast_template_replaces_per_note_long_run_decay(self):
        body = ",".join("12345678123456781234")
        parsed = parse_chart(f"(180){{32}}{body},E")
        common = {
            "duration_s": parsed.chart_end_time_s,
            "window_seconds": 2,
            "window_count": 1,
            "simple_run_decay_exponent": 0,
            "alternating_idle_multiplier": 0.2,
            "idle_distance_weight": 0,
            "takeover_weight": 0,
            "fast_jump_weight": 0,
        }
        no_template = score_sweep_burst(tuple(parsed.events), **common)
        solo_template = score_sweep_burst(
            tuple(parsed.events),
            solo_fast_base_multiplier=0.85,
            solo_fast_motion_multiplier=0.5,
            **common,
        )
        self.assertAlmostEqual(
            solo_template.base_density,
            no_template.base_density * 0.85,
        )

        slower = parse_chart(f"(180){{16}}{body},E")
        slower_common = dict(common, duration_s=slower.chart_end_time_s)
        slower_plain = score_sweep_burst(tuple(slower.events), **slower_common)
        slower_template = score_sweep_burst(
            tuple(slower.events),
            solo_fast_base_multiplier=0.85,
            solo_fast_motion_multiplier=0.5,
            **slower_common,
        )
        self.assertAlmostEqual(
            slower_template.base_density,
            slower_plain.base_density,
        )

    def test_ultra_long_fast_single_hand_uses_stronger_template(self):
        body = ",".join("12345678" * 7)
        parsed = parse_chart(f"(180){{32}}{body},E")
        common = {
            "duration_s": parsed.chart_end_time_s,
            "window_seconds": 3,
            "window_count": 1,
            "simple_run_decay_exponent": 0,
            "idle_distance_weight": 0,
            "takeover_weight": 0,
            "fast_jump_weight": 0,
        }
        plain = score_sweep_burst(tuple(parsed.events), **common)
        templated = score_sweep_burst(
            tuple(parsed.events),
            solo_fast_base_multiplier=0.85,
            solo_fast_long_base_multiplier=0.4,
            solo_fast_motion_multiplier=0.5,
            **common,
        )
        self.assertAlmostEqual(templated.base_density, plain.base_density * 0.4)

    def test_takeover_bonus_requires_same_direction_when_requested(self):
        common = {
            "window_seconds": 3,
            "window_count": 1,
            "idle_distance_weight": 0,
            "fast_jump_weight": 0,
            "same_direction_connection_bonus": 0,
            "same_direction_handoff_bonus": 0,
            "pattern_motion_floor": 1,
        }

        def score(body, same_direction_only):
            parsed = parse_chart(f"(180){{16}}{body},E")
            return score_sweep_burst(
                tuple(parsed.events),
                duration_s=parsed.chart_end_time_s,
                takeover_same_direction_only=same_direction_only,
                **common,
            )

        opposite = score("1,2,3,8,7,6", False)
        opposite_filtered = score("1,2,3,8,7,6", True)
        self.assertAlmostEqual(
            opposite.motion_density - opposite_filtered.motion_density,
            1 / 3,
        )
        same = score("1,2,3,8,1,2", False)
        same_filtered = score("1,2,3,8,1,2", True)
        self.assertGreater(same.motion_density, 0)
        self.assertAlmostEqual(same.motion_density, same_filtered.motion_density)


if __name__ == "__main__":
    unittest.main()
