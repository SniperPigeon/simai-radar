"""Explainable Slide motion, sequence, context, and global aggregation."""

import math
import unittest

from mairadar.analysis import ChartAnalyzer
from mairadar.analysis.features import SlidePressureAnalyzer
from mairadar.analysis.features.slide import (
    _effective_slide_count,
    slide_pressure_breakdown,
)
from mairadar.parser import parse_chart


def breakdown(text: str):
    parsed = parse_chart(text)
    if not parsed.complete:
        raise AssertionError(parsed.diagnostics)
    duration = max(parsed.chart_end_time_s, parsed.last_event_end_s or 0.0)
    return slide_pressure_breakdown(tuple(parsed.events), duration), duration


class SlidePressureTests(unittest.TestCase):
    def test_effective_sequence_length_is_linear_through_eight_then_logarithmic(self):
        self.assertEqual(_effective_slide_count(1), 1.0)
        self.assertEqual(_effective_slide_count(8), 8.0)
        self.assertAlmostEqual(_effective_slide_count(16), 8 + 8 * math.log(2))
        self.assertAlmostEqual(_effective_slide_count(32), 8 + 8 * math.log(4))
        with self.assertRaises(ValueError):
            _effective_slide_count(0)

    def test_reference_path_has_unit_motion_pressure(self):
        result, _ = breakdown("(120){4}1-5[10:1],,E")
        [section] = result.sections
        self.assertAlmostEqual(section.mean_movement_pressure, 1.0)
        self.assertEqual(section.context_per_slide, 0.0)
        self.assertAlmostEqual(section.intensity, 1.0)

    def test_motion_uses_only_square_root_length(self):
        fast, _ = breakdown("(120){4}1-5[0.5##0.1],,E")
        slow, _ = breakdown("(120){4}1-5[0.5##1.0],,E")
        long_at_reference_speed, _ = breakdown(
            "(120){4}1<1[0.5##0.64],,E"
        )
        self.assertAlmostEqual(fast.sections[0].mean_movement_pressure, 1.0)
        self.assertAlmostEqual(slow.sections[0].mean_movement_pressure, 1.0)
        self.assertAlmostEqual(
            long_at_reference_speed.sections[0].mean_movement_pressure,
            math.sqrt(64 / 20),
        )

    def test_launch_endpoint_includes_tap_and_weights_touch_more(self):
        tap, _ = breakdown("(120){4}1-5[10:1],2,E")
        touch, _ = breakdown("(120){4}1-5[10:1],A1,E")
        self.assertEqual(tap.sections[0].context_per_slide, 1.0)
        self.assertAlmostEqual(tap.sections[0].intensity, 1.5)
        self.assertEqual(touch.sections[0].context_per_slide, 1.5)
        self.assertAlmostEqual(touch.sections[0].intensity, 1.75)
        self.assertGreater(touch.global_pressure, tap.global_pressure)

    def test_dotted_eighth_sequence_survives_interleaved_objects(self):
        result, _ = breakdown(
            "(120){16}1-5[10:1],3,A1,2-6[10:1],E"
        )
        [section] = result.sections
        self.assertEqual((section.slide_count, section.onset_count), (2, 2))
        self.assertAlmostEqual(section.cadence_factor, 4 / 3)
        self.assertGreater(section.context_per_slide, 0)
        self.assertAlmostEqual(
            section.intensity,
            section.mean_movement_pressure * section.cadence_factor
            + 0.5 * section.context_per_slide,
        )
        self.assertAlmostEqual(
            section.pressure,
            math.sqrt(section.effective_slide_count) * section.intensity,
        )

    def test_actual_seconds_make_the_same_beat_sequence_harder_at_high_bpm(self):
        slow, _ = breakdown(
            "(120){4}1-5[0.5##0.2],2-6[0.5##0.2],E"
        )
        fast, _ = breakdown(
            "(240){4}1-5[0.5##0.2],2-6[0.5##0.2],E"
        )
        self.assertEqual(slow.sections[0].cadence_factor, 1.0)
        self.assertEqual(fast.sections[0].cadence_factor, 2.0)
        self.assertGreater(fast.global_pressure, slow.global_pressure)

    def test_gap_over_one_beat_starts_a_new_section(self):
        result, _ = breakdown("(120){4}1-5[10:1],,2-6[10:1],E")
        self.assertEqual(len(result.sections), 2)
        self.assertEqual([section.slide_count for section in result.sections], [1, 1])

    def test_simultaneous_slides_are_one_onset_cluster_not_zero_interval(self):
        result, _ = breakdown(
            "(120){4}1-5[10:1]/2-6[10:1],,E"
        )
        [section] = result.sections
        self.assertEqual((section.slide_count, section.onset_count), (2, 1))
        self.assertEqual(section.cadence_factor, 1.0)

    def test_sub_frame_stagger_is_merged_into_one_onset_cluster(self):
        result, _ = breakdown(
            "(240){9999}1-5[0.5##0.2],{4}2-6[0.5##0.2],E"
        )
        [section] = result.sections
        self.assertEqual((section.slide_count, section.onset_count), (2, 1))
        self.assertEqual(section.cadence_factor, 1.0)
        self.assertLess(result.global_pressure, 20)

    def test_stagger_over_one_frame_keeps_its_cadence_edge(self):
        result, _ = breakdown(
            "(120){100}1-5[0.5##0.2],{4}2-6[0.5##0.2],E"
        )
        [section] = result.sections
        self.assertEqual((section.slide_count, section.onset_count), (2, 2))
        self.assertAlmostEqual(section.cadence_factor, 25.0)

    def test_no_head_slides_use_reconstructed_declaration_beats(self):
        result, _ = breakdown(
            "(120){4}1?-5[10:1],2?-6[10:1],E"
        )
        [section] = result.sections
        self.assertEqual((section.slide_count, section.onset_count), (2, 2))
        self.assertEqual(section.cadence_factor, 1.0)

    def test_zero_duration_slide_is_skipped_without_failing_the_chart(self):
        result, _ = breakdown("(120){4}1-5[4:0],,E")
        analyzed = ChartAnalyzer({"slide": SlidePressureAnalyzer}).analyze(
            parse_chart("(120){4}1-5[4:0],,E")
        )
        self.assertEqual(result.global_pressure, 0.0)
        self.assertEqual(result.sections, ())
        self.assertTrue(analyzed.features["slide"].success)
        self.assertEqual(analyzed.features["slide"].data, 0.0)

    def test_only_zero_duration_branch_is_removed_from_a_shared_head_group(self):
        result, _ = breakdown(
            "(120){4}1-5[4:0]*-5[10:1],,E"
        )
        [section] = result.sections
        self.assertEqual(section.slide_count, 1)
        self.assertAlmostEqual(section.mean_movement_pressure, 1.0)

    def test_global_pressure_uses_every_section(self):
        result, duration = breakdown(
            "(120){4}1-5[10:1],,2-6[10:1],,,E"
        )
        self.assertEqual(len(result.sections), 2)
        energy = sum(section.pressure ** 2 for section in result.sections)
        self.assertAlmostEqual(
            result.global_pressure,
            math.sqrt(60 * energy / duration),
        )
        self.assertGreater(
            result.global_pressure,
            math.sqrt(60 * result.sections[0].pressure ** 2 / duration),
        )

    def test_positive_empty_chart_is_zero_and_zero_duration_is_unavailable(self):
        empty = ChartAnalyzer({"slide": SlidePressureAnalyzer}).analyze(
            parse_chart("(120){4},,E")
        )
        zeroth = ChartAnalyzer({"slide": SlidePressureAnalyzer}).analyze(
            parse_chart("E")
        )
        self.assertEqual(empty.features["slide"].data, 0.0)
        self.assertFalse(zeroth.features["slide"].success)


if __name__ == "__main__":
    unittest.main()
