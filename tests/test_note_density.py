"""Corrected fixed-window note density and maimai sensor grouping."""

import unittest

from mairadar.analysis import ChartAnalyzer
from mairadar.analysis.features import NoteDensityAnalyzer
from mairadar.analysis.features.note_density import TOUCH_ADJACENCY
from mairadar.parser import parse_chart


def density(text: str) -> float:
    result = ChartAnalyzer({"note_density": NoteDensityAnalyzer}).analyze(parse_chart(text))
    feature = result.features["note_density"]
    if not feature.success:
        raise AssertionError(result.diagnostics)
    return feature.data


class SensorAdjacencyTests(unittest.TestCase):
    def test_rotational_sensor_graph_matches_shared_edges(self):
        self.assertEqual(
            TOUCH_ADJACENCY["A1"],
            frozenset({"B1", "D1", "D2", "E1", "E2"}),
        )
        self.assertEqual(
            TOUCH_ADJACENCY["B1"],
            frozenset({"A1", "B2", "B8", "C", "E1", "E2"}),
        )
        self.assertEqual(TOUCH_ADJACENCY["D1"], frozenset({"A8", "A1"}))
        self.assertEqual(
            TOUCH_ADJACENCY["E1"],
            frozenset({"A8", "A1", "B8", "B1"}),
        )

    def test_c_is_one_logical_region_adjacent_to_every_b_region(self):
        self.assertEqual(TOUCH_ADJACENCY["C"], frozenset(f"B{i}" for i in range(1, 9)))
        self.assertNotIn("C1", TOUCH_ADJACENCY)
        self.assertNotIn("C2", TOUCH_ADJACENCY)

    def test_corner_contact_is_not_an_edge(self):
        self.assertNotIn("E1", TOUCH_ADJACENCY["D1"])
        self.assertNotIn("A2", TOUCH_ADJACENCY["A1"])


class NoteDensityTests(unittest.TestCase):
    def test_uniform_and_bursty_windows_use_population_variation(self):
        uniform = density("(120){4}1,2,3,1,2,3,E")
        burst = density("(120){4}1/2/3/4/5/6,,,,,,E")
        self.assertEqual(uniform, 2.0)
        # Densities [4, 0]: mean=2, CV=1, score=2*(1+0.3).
        self.assertEqual(burst, 2.6)

    def test_partial_final_window_is_zero_padded_to_1_5_seconds(self):
        # Duration 2.0, one Tap at 1.5: densities [0, 2/3].
        self.assertAlmostEqual(density("(120){4},,,1,E"), (1 / 3) * 1.3)

    def test_eighth_note_hold_boundary_uses_exact_beat_axis(self):
        self.assertEqual(density("(120){4}1h[8:1]/2h[4:1],E"), 2.0)
        self.assertEqual(
            density("(120){8}1h[8:1],(240)2h[8:1],E"),
            2 / 1.5,
        )

    def test_slide_group_sums_branches_before_dividing_by_64(self):
        short_group = density("(120){4}1?-5[4:1]*-5[4:1]*-5[4:1],E")
        long_group = density("(120){4}1?-5[4:1]*-5[4:1]*-5[4:1]*-5[4:1],E")
        self.assertEqual(short_group, 1 / 1.5)  # total length 60 -> 1
        self.assertEqual(long_group, 2 / 1.5)  # total length 80 -> 2

    def test_shared_head_branches_form_one_slide_group_but_keep_the_head_tap(self):
        score = density("(120){4}1-5[4:1]*-5[4:1]*-5[4:1],E")
        self.assertEqual(score, 2 / 1.5)  # head Tap 1 + ceil(60/64)

    def test_connected_slide_uses_all_segment_lengths(self):
        # Each full circle has bar_count=64; the connected group therefore weighs 2.
        self.assertEqual(density("(120){4}1?<1<1[4:2],E"), 2 / 1.5)

    def test_simultaneous_touch_uses_spatial_connected_components(self):
        self.assertEqual(density("(120)A1/E1/B8,E"), 1 / 1.5)
        self.assertEqual(density("(120)D1/E1,E"), 2 / 1.5)

    def test_touch_pair_can_span_exactly_one_sixteenth_note(self):
        self.assertEqual(density("(120){16}A1,B1,E"), 1 / 1.5)
        self.assertEqual(density("(120){8}A1,B1,E"), 2 / 1.5)

    def test_touch_temporal_group_never_chains_across_three_time_points(self):
        # A1--B1 and B1--C are both adjacent, but the first pair wins and C
        # remains a second workload group.
        self.assertEqual(density("(120){16}A1,B1,C,E"), 2 / 1.5)

    def test_touch_hold_follows_touch_grouping(self):
        self.assertEqual(density("(120){16}A1h[4:1]/B1,C,E"), 1 / 1.5)

    def test_positive_empty_chart_is_zero_and_zero_duration_is_unavailable(self):
        self.assertEqual(density("(120){4},,E"), 0.0)
        result = ChartAnalyzer({"note_density": NoteDensityAnalyzer}).analyze(parse_chart("E"))
        self.assertFalse(result.features["note_density"].success)
        self.assertIsNone(result.features["note_density"].data)


if __name__ == "__main__":
    unittest.main()
