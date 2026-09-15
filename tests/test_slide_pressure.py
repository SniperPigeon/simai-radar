"""Independent Slide misalignment and sequence-strength features."""

import math
import unittest
from fractions import Fraction

from mairadar.analysis import ChartAnalyzer
from mairadar.analysis.features import (
    SlideCumulateAnalyzer,
    SlideSequenceAnalyzer,
    SlideTrickyAnalyzer,
)
from mairadar.analysis.features.slide import (
    CONCURRENCY_WEIGHT,
    MULTI_SLIDE_UPLIFT,
    PENDING_SLIDE_HEAD_MULTIPLIER,
    SAME_POSITION_MULTIPLIER,
    TRICKY_OBJECT_CAP,
    TRICKY_SPEED_EXPONENT,
    TRICKY_SPEED_MAX_FACTOR,
    TRICKY_SPEED_REFERENCE_EIGHTH_BPM,
    TRICKY_TOP_COUNT,
    _WorkloadPoint,
    _sequence_length_factor,
    _sweep_adjusted_button_total,
    _top_unique_tricky_loads,
    slide_feature_breakdown,
)
from mairadar.parser import parse_chart


def breakdown(text: str):
    parsed = parse_chart(text)
    if not parsed.complete:
        raise AssertionError(parsed.diagnostics)
    duration = max(parsed.chart_end_time_s, parsed.last_event_end_s or 0.0)
    return slide_feature_breakdown(tuple(parsed.events), duration)


class SlidePressureTests(unittest.TestCase):
    def test_internal_taps_are_linear_and_launch_objects_are_halved(self):
        result = breakdown("(120){16}1-5[10:1],2,3,4,5,E")
        [section] = result.sections
        [point] = result.tricky_points
        self.assertAlmostEqual(
            section.internal_interference,
            3 * point.ordinary_button_speed_factor,
        )
        self.assertEqual(section.launch_interference, 0.5)
        self.assertAlmostEqual(
            section.tricky_intensity,
            3 * point.ordinary_button_speed_factor + 0.5,
        )

    def test_touch_keeps_its_extra_weight_at_the_exact_launch(self):
        result = breakdown("(120){4}1-5[10:1],A1,E")
        [section] = result.sections
        self.assertEqual(section.internal_interference, 0.0)
        self.assertEqual(section.launch_interference, 0.75)
        self.assertAlmostEqual(result.tricky_total_load, 0.75)

    def test_slide_head_at_launch_adds_its_corresponding_body_before_halving(self):
        ordinary = breakdown("(120){4}1-5[10:1],2-6[10:1],E").sections[0]
        shared = breakdown(
            "(120){4}1-5[10:1],2-6[10:1]*-4[10:1],E"
        ).sections[0]
        zero_wait = breakdown(
            "(120){4}1-5[10:1],2-6[0##0.2],E"
        ).tricky_points[0]
        self.assertEqual(ordinary.launch_interference, 1.0)
        self.assertEqual(shared.launch_interference, 1.5)
        self.assertEqual(zero_wait.launch, 1.0)

    def test_simultaneous_shared_paths_count_each_launched_slide_body(self):
        result = breakdown(
            "(120){4}1-5[1##0.2]*-3[1##0.2],2-6[10:1],E"
        )
        self.assertEqual(result.tricky_points[1].launch, 1.0)

    def test_simultaneous_internal_batch_is_added_once(self):
        result = breakdown("(120){4}1-5[10:1]/2/3,,E")
        [section] = result.sections
        self.assertAlmostEqual(section.internal_interference, 2.0)

    def test_double_slide_wait_assigns_each_external_object_only_once(self):
        result = breakdown("(120){4}1-5[10:1]/2-6[10:1]/3,,E")
        [section] = result.sections
        self.assertEqual((section.slide_count, section.onset_count), (2, 1))
        [point] = result.tricky_points
        self.assertEqual(section.internal_interference, 1.0)
        self.assertAlmostEqual(
            result.tricky,
            point.load / TRICKY_TOP_COUNT,
        )

    def test_single_sweep_decays_from_fourth_timestamp(self):
        points = [
            _WorkloadPoint(
                ("event", index),
                float(index),
                1.0,
                "button",
                beat,
                str(position),
            )
            for index, (beat, position) in enumerate(
                zip(map(Fraction, range(5)), range(1, 6)),
                start=1,
            )
        ]
        expected = 3 + 1 / math.log2(3) + 1 / math.log2(4)
        self.assertAlmostEqual(_sweep_adjusted_button_total(points, set()), expected)

    def test_double_sweep_has_twice_the_decayed_weight(self):
        points = []
        for beat in range(5):
            for lane in (1, 5):
                position = (lane + beat - 1) % 8 + 1
                points.append(_WorkloadPoint(
                    ("event", beat, lane),
                    float(beat),
                    1.0,
                    "button",
                    Fraction(beat),
                    str(position),
                ))
        expected = 2 * (3 + 1 / math.log2(3) + 1 / math.log2(4))
        self.assertAlmostEqual(_sweep_adjusted_button_total(points, set()), expected)

    def test_same_position_and_pending_slide_head_use_larger_multiplier(self):
        ordinary = _WorkloadPoint(
            ("event", 1), 0.0, 1.0, "button", Fraction(0), "1"
        )
        slide_head = _WorkloadPoint(
            ("event", 2), 0.0, 1.0, "button", Fraction(0), "1",
            slide_head_body_weight=1.0,
        )
        self.assertEqual(
            _sweep_adjusted_button_total([ordinary], {"1"}),
            SAME_POSITION_MULTIPLIER,
        )
        self.assertEqual(
            _sweep_adjusted_button_total([slide_head], {"1"}),
            PENDING_SLIDE_HEAD_MULTIPLIER,
        )

    def test_display_only_star_stays_at_tap_weight(self):
        result = breakdown("(120){4}1-5[10:1]/2$,,E")
        self.assertEqual(result.sections[0].internal_interference, 1.0)

    def test_actual_pending_slide_head_gets_double_tap_weight(self):
        result = breakdown("(120){8}1-5[4:1],2-6[4:1],,E")
        # The later head contributes 2 while waiting; its body then contributes
        # 1 during the first Slide's one-beat active phase.
        self.assertEqual(result.tricky_points[0].internal, 3.0)

    def test_one_object_uses_the_nearest_distinct_pending_configuration(self):
        result = breakdown(
            "(120){4}1-5[1##0.2],{8}2-6[1##0.2],3,,,,E"
        )
        first, second = result.tricky_points
        # Tap 3 at 0.75 s belongs only to the nearer first launch. Their own
        # heads remain excluded; the first Slide launch later adds 1 to second.
        self.assertAlmostEqual(
            first.internal,
            2 + first.ordinary_button_speed_factor,
        )
        self.assertEqual(second.internal, 1.0)

    def test_active_interference_stops_one_beat_after_launch(self):
        result = breakdown("(120){8}1-5[4:8],,,1,,,,1,E")
        [point] = result.tricky_points
        # Same-position Tap at 0.75 s is active-phase weight 1, not the waiting
        # multiplier 1.5. The later Tap at 1.75 s is beyond launch + one beat.
        self.assertAlmostEqual(
            point.internal,
            point.ordinary_button_speed_factor,
        )

    def test_speed_factor_uses_180_bpm_eighths_and_caps_fast_patterns(self):
        slow = breakdown("(120){8}1-5[1##0.2],2,3,4,5,E").tricky_points[0]
        boundary = breakdown("(180){8}1-5[8:1],2,3,E").tricky_points[0]
        fast = breakdown("(150){16}1-5[8:1],2,3,4,5,E").tricky_points[0]
        expected = (
            120 / TRICKY_SPEED_REFERENCE_EIGHTH_BPM
        ) ** TRICKY_SPEED_EXPONENT
        self.assertAlmostEqual(slow.ordinary_button_speed_factor, expected)
        self.assertAlmostEqual(slow.internal, 3 * expected)
        self.assertEqual(slow.launch, 0.5)
        self.assertEqual(boundary.ordinary_button_speed_factor, 1.0)
        self.assertEqual(
            fast.ordinary_button_speed_factor,
            TRICKY_SPEED_MAX_FACTOR,
        )

    def test_touch_groups_are_capped_at_two(self):
        result = breakdown("(120){4}1-5[4##1],A1,B2,C,D3,E")
        self.assertEqual(result.sections[0].internal_interference, 3.0)

    def test_configuration_object_count_scales_down_above_sixteen(self):
        result = breakdown(
            "(120){20}1-5[2##0.2],"
            "2,4,7,3,8,5,2,6,3,7,4,8,2,5,3,6,4,7,5,E"
        )
        [point] = result.tricky_points
        self.assertGreater(point.logical_object_count, TRICKY_OBJECT_CAP)
        self.assertAlmostEqual(
            point.object_cap_factor,
            TRICKY_OBJECT_CAP / point.logical_object_count,
        )

    def test_individual_wait_duration_does_not_scale_tricky_load(self):
        reference = breakdown("(120){4}1-5[0.5##0.2]/2,,E").sections[0]
        fast = breakdown("(120){4}1-5[0.25##0.2]/2,,E").sections[0]
        very_fast = breakdown("(120){4}1-5[0.01##0.2]/2,,E").sections[0]
        zero_wait = breakdown("(120){4}1-5[0##0.2]/2,,E").sections[0]
        self.assertAlmostEqual(reference.tricky_intensity, 1.0)
        self.assertAlmostEqual(fast.tricky_intensity, 1.0)
        self.assertAlmostEqual(very_fast.tricky_intensity, 1.0)
        self.assertAlmostEqual(zero_wait.tricky_intensity, 0.5)

    def test_tricky_is_not_divided_by_chart_duration(self):
        parsed = parse_chart("(120){4}1-5[0.5##0.2]/2,E")
        result = slide_feature_breakdown(tuple(parsed.events), 120.0)
        [point] = result.tricky_points
        self.assertAlmostEqual(result.tricky_total_load, point.load)
        self.assertAlmostEqual(result.tricky, point.load / TRICKY_TOP_COUNT)

    def test_tricky_uses_zero_padded_top_five_mean(self):
        result = breakdown(
            "(120){4}1-5[0.5##0.2]/2,,3-7[0.5##0.2]/4/5,,E"
        )
        peak = max(result.tricky_points, key=lambda point: point.load)
        top = sorted(
            (point.load for point in result.tricky_points),
            reverse=True,
        )[:TRICKY_TOP_COUNT]
        self.assertEqual(result.tricky, sum(top) / TRICKY_TOP_COUNT)
        self.assertEqual(result.tricky_total_load, sum(top))
        self.assertEqual(result.tricky_peak_time_s, peak.time_s)

    def test_unique_group_loads_use_buckets_and_ignore_repeated_values(self):
        loads = [10.0, 10.0 + 4e-7, 9.0, 8.0, 7.0, 6.0, 5.0]
        self.assertEqual(
            _top_unique_tricky_loads(loads),
            (10.0 + 4e-7, 9.0, 8.0, 7.0, 6.0),
        )

    def test_continuous_sequence_length_bonus_starts_after_three_onsets(self):
        self.assertEqual(_sequence_length_factor(3), 1.0)
        expected = math.sqrt((3 + 3 * math.log(4 / 3)) / 3)
        self.assertAlmostEqual(_sequence_length_factor(4), expected)

    def test_two_quarter_spaced_slides_form_a_sequence_without_interference_input(self):
        result = breakdown("(120){4}1-5[10:1],2-6[10:1],E")
        [section] = result.sections
        self.assertEqual((section.slide_count, section.onset_count), (2, 2))
        self.assertEqual(section.cadence_factor, 1.0)
        self.assertEqual(section.concurrency_pressure, 0.0)
        self.assertEqual(section.sequence_intensity, 1.0)
        self.assertEqual(result.sequence, 1.0)

    def test_dotted_eighth_sequence_survives_interleaved_objects(self):
        result = breakdown("(120){16}1-5[10:1],3,A1,2-6[10:1],E")
        [section] = result.sections
        self.assertEqual((section.slide_count, section.onset_count), (2, 2))
        self.assertAlmostEqual(section.cadence_factor, 4 / 3)
        self.assertAlmostEqual(section.sequence_intensity, 4 / 3)
        self.assertGreater(section.tricky_intensity, 0)
        values = section.tricky_cluster_values
        mean = sum(values) / len(values)
        self.assertAlmostEqual(section.tricky_intensity, mean)
        self.assertAlmostEqual(section.tricky_load, sum(values))

    def test_concurrency_distinguishes_single_double_and_shared_head(self):
        ordinary = breakdown("(120){4}1-5[10:1],,E").sections[0]
        double = breakdown("(120){4}1-5[10:1]/2-6[10:1],,E").sections[0]
        shared = breakdown("(120){4}1-5[10:1]*-3[10:1],,E").sections[0]
        self.assertEqual(ordinary.concurrency_pressure, 0.0)
        self.assertEqual(double.concurrency_pressure, 1.0)
        self.assertAlmostEqual(shared.concurrency_pressure, math.sqrt(2) - 1)
        self.assertEqual(double.sequence_intensity, CONCURRENCY_WEIGHT)
        self.assertAlmostEqual(
            shared.sequence_intensity,
            CONCURRENCY_WEIGHT * (math.sqrt(2) - 1),
        )

    def test_unusual_three_path_shared_head_remains_defined_for_utage(self):
        result = breakdown(
            "(120){4}1-5[10:1]*-3[10:1]*-4[10:1],,E"
        )
        [section] = result.sections
        self.assertAlmostEqual(section.concurrency_pressure, math.sqrt(3) - 1)

    def test_shared_head_paths_receive_small_post_interference_uplift(self):
        result = breakdown("(120){4}1-5[10:1]*-3[10:1]/2,,E")
        expected = 1 + MULTI_SLIDE_UPLIFT * (math.sqrt(2) - 1)
        self.assertAlmostEqual(result.tricky, expected / TRICKY_TOP_COUNT)

    def test_multi_head_multi_path_configuration_uses_generic_uplift(self):
        result = breakdown(
            "(120){4}1-5[10:1]*-3[10:1]/2-6[10:1]*-4[10:1]/3,,E"
        )
        [point] = result.tricky_points
        expected = 1 + MULTI_SLIDE_UPLIFT * (2 * math.sqrt(2) - 1)
        self.assertEqual((point.head_count, point.slide_count), (2, 4))
        self.assertAlmostEqual(point.configuration_multiplier, expected)
        self.assertAlmostEqual(point.load, expected)

    def test_sub_frame_stagger_merges_but_longer_stagger_keeps_cadence(self):
        merged = breakdown(
            "(240){9999}1-5[0.5##0.2],{4}2-6[0.5##0.2],E"
        ).sections[0]
        separate = breakdown(
            "(120){100}1-5[0.5##0.2],{4}2-6[0.5##0.2],E"
        ).sections[0]
        self.assertEqual(merged.onset_count, 1)
        self.assertEqual(merged.cadence_factor, 1.0)
        self.assertEqual(separate.onset_count, 2)
        self.assertAlmostEqual(separate.cadence_factor, 25.0)

    def test_gap_over_one_beat_starts_a_new_section(self):
        result = breakdown("(120){4}1-5[10:1],,2-6[10:1],E")
        self.assertEqual(len(result.sections), 2)
        self.assertEqual(result.sequence, 0.0)

    def test_zero_duration_slide_is_skipped_without_failing_other_paths(self):
        empty = breakdown("(120){4}1-5[4:0],,E")
        mixed = breakdown("(120){4}1-5[4:0]*-5[10:1],,E")
        self.assertEqual(empty.sections, ())
        self.assertEqual((empty.tricky, empty.sequence), (0.0, 0.0))
        self.assertEqual(mixed.sections[0].slide_count, 1)

    def test_analyzers_return_independent_values_and_handle_empty_duration(self):
        parsed = parse_chart("(120){4}1-5[10:1],A1,E")
        result = ChartAnalyzer({
            "slide_tricky": SlideTrickyAnalyzer,
            "slide_cumulate": SlideCumulateAnalyzer,
            "slide_sequence": SlideSequenceAnalyzer,
        }).analyze(parsed)
        self.assertAlmostEqual(result.features["slide_tricky"].data, 0.15)
        self.assertAlmostEqual(result.features["slide_cumulate"].data, 0.75)
        self.assertEqual(result.features["slide_sequence"].data, 0.0)

        empty = ChartAnalyzer({
            "slide_tricky": SlideTrickyAnalyzer,
            "slide_cumulate": SlideCumulateAnalyzer,
            "slide_sequence": SlideSequenceAnalyzer,
        }).analyze(parse_chart("E"))
        self.assertFalse(empty.features["slide_tricky"].success)
        self.assertFalse(empty.features["slide_cumulate"].success)
        self.assertFalse(empty.features["slide_sequence"].success)


if __name__ == "__main__":
    unittest.main()
