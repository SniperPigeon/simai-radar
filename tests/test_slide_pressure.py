"""Independent Slide misalignment and sequence-strength features."""

import math
import unittest

from mairadar.analysis import ChartAnalyzer
from mairadar.analysis.features import (
    SlideSequenceAnalyzer,
    SlideTrickyAnalyzer,
)
from mairadar.analysis.features.slide import (
    CONCURRENCY_WEIGHT,
    INTERNAL_GROWTH_ALPHA,
    _internal_total,
    _sequence_length_factor,
    _tricky_length_factor,
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
    def test_internal_growth_is_mild_n_log_n_with_alpha_point_35(self):
        self.assertEqual(INTERNAL_GROWTH_ALPHA, 0.35)
        self.assertEqual(_internal_total(0), 0.0)
        self.assertEqual(_internal_total(1), 1.0)
        for count in range(2, 9):
            expected = count + 0.35 * math.log2(math.factorial(count))
            self.assertAlmostEqual(_internal_total(count), expected)
        with self.assertRaises(ValueError):
            _internal_total(-1)

    def test_internal_objects_use_growth_but_launch_objects_stay_linear(self):
        result = breakdown("(120){16}1-5[10:1],2,3,4,5,E")
        [section] = result.sections
        self.assertAlmostEqual(section.internal_interference, _internal_total(3))
        self.assertEqual(section.launch_interference, 1.0)
        self.assertAlmostEqual(
            section.tricky_intensity,
            _internal_total(3) + 1,
        )

    def test_touch_keeps_its_extra_weight_at_the_exact_launch(self):
        result = breakdown("(120){4}1-5[10:1],A1,E")
        [section] = result.sections
        self.assertEqual(section.internal_interference, 0.0)
        self.assertEqual(section.launch_interference, 1.5)
        self.assertAlmostEqual(result.tricky_total_load, 1.5)

    def test_simultaneous_internal_batch_has_no_arbitrary_order(self):
        result = breakdown("(120){4}1-5[10:1]/2/3,,E")
        [section] = result.sections
        self.assertAlmostEqual(section.internal_interference, _internal_total(2))

    def test_double_slide_wait_assigns_each_external_object_only_once(self):
        result = breakdown("(120){4}1-5[10:1]/2-6[10:1]/3,,E")
        [section] = result.sections
        self.assertEqual((section.slide_count, section.onset_count), (2, 1))
        self.assertEqual(section.internal_interference, 1.0)

    def test_all_internal_objects_remain_uncapped(self):
        result = breakdown(
            "(120){20}1-5[1##0.2],2,3,4,5,6,7,8,2,3,,E"
        )
        [section] = result.sections
        self.assertAlmostEqual(section.internal_interference, _internal_total(9))
        self.assertAlmostEqual(result.tricky_total_load, 0.5 * _internal_total(9))

    def test_half_second_wait_is_unit_density_with_four_times_cap(self):
        reference = breakdown("(120){4}1-5[0.5##0.2]/2,,E").sections[0]
        fast = breakdown("(120){4}1-5[0.25##0.2]/2,,E").sections[0]
        very_fast = breakdown("(120){4}1-5[0.01##0.2]/2,,E").sections[0]
        zero_wait = breakdown("(120){4}1-5[0##0.2]/2,,E").sections[0]
        self.assertAlmostEqual(reference.mean_time_density_factor, 1.0)
        self.assertAlmostEqual(fast.mean_time_density_factor, 2.0)
        self.assertAlmostEqual(very_fast.mean_time_density_factor, 4.0)
        self.assertAlmostEqual(zero_wait.mean_time_density_factor, 4.0)
        self.assertAlmostEqual(reference.tricky_intensity, 1.0)
        self.assertAlmostEqual(fast.tricky_intensity, 2.0)
        self.assertAlmostEqual(very_fast.tricky_intensity, 4.0)
        self.assertAlmostEqual(zero_wait.tricky_intensity, 4.0)

    def test_global_time_density_downweights_one_isolated_tricky_slide(self):
        parsed = parse_chart("(120){4}1-5[0.5##0.2]/2,E")
        result = slide_feature_breakdown(tuple(parsed.events), 120.0)
        self.assertAlmostEqual(result.tricky_total_load, 1.0)
        self.assertAlmostEqual(result.tricky_time_units, 240.0)
        self.assertAlmostEqual(result.tricky, 1 / 240)

    def test_sections_add_linearly_before_total_time_normalization(self):
        result = breakdown("(120){4}1-5[0.5##0.2]/2,,3-7[0.5##0.2]/4,,E")
        self.assertEqual(len(result.sections), 2)
        self.assertAlmostEqual(
            result.tricky_total_load,
            sum(section.tricky_load for section in result.sections),
        )
        self.assertAlmostEqual(
            result.tricky,
            result.tricky_total_load / result.tricky_time_units,
        )

    def test_tricky_long_sequence_bonus_starts_after_eight_onsets(self):
        self.assertEqual(_tricky_length_factor(8), 1.0)
        self.assertGreater(_tricky_length_factor(9), 1.0)
        expected = math.sqrt((8 + 8 * math.log(2)) / 8)
        self.assertAlmostEqual(_tricky_length_factor(16), expected)

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
            "slide_sequence": SlideSequenceAnalyzer,
        }).analyze(parsed)
        expected = 1.5 / 2
        self.assertAlmostEqual(result.features["slide_tricky"].data, expected)
        self.assertEqual(result.features["slide_sequence"].data, 0.0)

        empty = ChartAnalyzer({
            "slide_tricky": SlideTrickyAnalyzer,
            "slide_sequence": SlideSequenceAnalyzer,
        }).analyze(parse_chart("E"))
        self.assertFalse(empty.features["slide_tricky"].success)
        self.assertFalse(empty.features["slide_sequence"].success)


if __name__ == "__main__":
    unittest.main()
