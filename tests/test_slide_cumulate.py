"""Standalone historical Slide cumulative-pressure policy."""

import math
import unittest

from mairadar.analysis import AnalysisContext
from mairadar.analysis.features import SlideCumulateAnalyzer
from mairadar.analysis.features.slide_cumulate import (
    _cumulate_effective_length,
    _cumulate_internal_total,
    _cumulate_section_intensity,
    slide_cumulate_breakdown,
)
from mairadar.parser import parse_chart


def breakdown(text: str, *, duration_s: float | None = None):
    parsed = parse_chart(text)
    if not parsed.complete:
        raise AssertionError(parsed.diagnostics)
    duration = duration_s if duration_s is not None else max(
        parsed.chart_end_time_s,
        parsed.last_event_end_s or 0.0,
    )
    return slide_cumulate_breakdown(tuple(parsed.events), duration)


class SlideCumulateTests(unittest.TestCase):
    def test_internal_objects_grow_but_exact_launch_remains_linear(self):
        result = breakdown("(120){16}1-5[10:1],2,3,4,5,E")
        [section] = result.sections
        expected = _cumulate_internal_total(3) + 1
        self.assertAlmostEqual(section.cluster_loads[0], expected)
        self.assertAlmostEqual(section.total_load, expected)

    def test_touch_at_launch_keeps_weight_one_point_five(self):
        result = breakdown("(120){4}1-5[10:1],A1,E")
        self.assertAlmostEqual(result.sections[0].cluster_loads[0], 1.5)

    def test_double_wait_assigns_external_object_only_once(self):
        result = breakdown("(120){4}1-5[10:1]/2-6[10:1]/3,,E")
        [section] = result.sections
        self.assertEqual(section.onset_count, 1)
        self.assertEqual(section.cluster_loads, (1.0,))

    def test_individual_wait_duration_does_not_scale_load(self):
        values = [
            breakdown(f"(120){{4}}1-5[{wait}##0.2]/2,,E").sections[0].cluster_loads[0]
            for wait in ("0", "0.01", "0.25", "0.5")
        ]
        self.assertEqual(values, [1.0] * 4)

    def test_effective_length_is_linear_through_five_then_log_base_five(self):
        self.assertEqual(_cumulate_effective_length(5), 5.0)
        self.assertAlmostEqual(
            _cumulate_effective_length(10),
            5 + 5 * math.log(2, 5),
        )
        self.assertEqual(_cumulate_effective_length(25), 10.0)

    def test_section_intensity_is_eighty_percent_mean_twenty_percent_rms(self):
        loads = (1.0, 1.0, 4.0)
        mean = 2.0
        rms = math.sqrt(6)
        self.assertAlmostEqual(
            _cumulate_section_intensity(loads),
            0.8 * mean + 0.2 * rms,
        )

    def test_sections_add_then_divide_by_chart_half_second_units(self):
        result = breakdown(
            "(120){4}1-5[0.5##0.2]/2,,3-7[0.5##0.2]/4,,E"
        )
        self.assertEqual(len(result.sections), 2)
        self.assertAlmostEqual(
            result.total_load,
            sum(section.total_load for section in result.sections),
        )
        self.assertAlmostEqual(result.value, result.total_load / result.time_units)

    def test_sub_frame_slide_declarations_share_one_onset(self):
        result = breakdown(
            "(240){9999}1-5[0.5##0.2],{4}2-6[0.5##0.2]/3,E"
        )
        self.assertEqual(result.sections[0].onset_count, 1)

    def test_zero_duration_paths_are_skipped(self):
        empty = breakdown("(120){4}1-5[4:0],,E")
        mixed = breakdown("(120){4}1-5[4:0]*-5[10:1]/2,,E")
        self.assertEqual((empty.value, empty.sections), (0.0, ()))
        self.assertEqual(mixed.sections[0].cluster_loads, (1.0,))

    def test_positive_empty_chart_is_zero_and_zero_duration_is_unavailable(self):
        positive = breakdown("(120){4},,E")
        self.assertEqual(positive.value, 0.0)
        parsed = parse_chart("E")
        result = SlideCumulateAnalyzer().analyze(AnalysisContext(
            tuple(parsed.events),
            parsed.chart_end_time_s or 0.0,
            parsed.last_event_end_s,
        ))
        self.assertFalse(result.success)


if __name__ == "__main__":
    unittest.main()
