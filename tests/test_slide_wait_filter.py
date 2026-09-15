"""Modal wait filtering affects only Tricky targets and keeps path boundaries."""

from copy import deepcopy
from fractions import Fraction
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from mairadar.analysis.features import slide as s
from mairadar.parser import parse_chart


def groups_with_waits(waits):
    return [SimpleNamespace(declaration_time_s=0, events=[SimpleNamespace(start_time_s=w)])
            for w in waits]


class SlideWaitFilterTests(unittest.TestCase):
    def test_coarse_bucket_combines_close_waits(self):
        groups = groups_with_waits([0.20, 0.21, 0.22, 0.5, 0.5])
        self.assertAlmostEqual(s._modal_slide_wait_s(groups), 0.21)

    def test_tied_modes_choose_longer_and_zero_waits_do_not_dominate(self):
        self.assertEqual(s._modal_slide_wait_s(
            groups_with_waits([0, 0, 0.2, 0.2, 0.5, 0.5]),
        ), 0.5)
        self.assertIsNone(s._modal_slide_wait_s(groups_with_waits([0, 0])))

    def test_half_bucket_boundary_tolerates_float_noise(self):
        groups = groups_with_waits([0.225 - 1e-12, 0.225 + 1e-12, 0.25, 0.2, 0.2])
        self.assertAlmostEqual(s._modal_slide_wait_s(groups), 0.225)

    def test_exactly_fourfold_is_kept_and_only_long_sibling_removed(self):
        parsed = parse_chart("(120){4}1-5[1##0.2]*-3[1.001##0.2],,,E")
        [cluster] = s._build_onset_clusters(s._build_slide_groups(tuple(parsed.events)))
        snapshot = deepcopy(cluster)
        filtered = s._filter_tricky_cluster(cluster, 0.25)
        self.assertEqual(len(filtered.groups[0].events), 1)
        self.assertEqual(filtered.groups[0].launch_time_s, 1)
        self.assertEqual(filtered.groups[0].active_intervals, ((1, 1.2),))
        self.assertEqual(cluster, snapshot)

    def test_all_abnormal_paths_remove_target_but_missing_mode_keeps_it(self):
        parsed = parse_chart("(120){4}1-5[2##0.2],,,,E")
        [cluster] = s._build_onset_clusters(s._build_slide_groups(tuple(parsed.events)))
        self.assertIsNone(s._filter_tricky_cluster(cluster, 0.25))
        self.assertIs(s._filter_tricky_cluster(cluster, None), cluster)

    def test_partial_removal_discards_interference_outside_remaining_window(self):
        parsed = parse_chart("(120){4}1-5[0.5##0.2]*-3[2##0.2],,,,E")
        [cluster] = s._build_onset_clusters(s._build_slide_groups(tuple(parsed.events)))
        late = s._WorkloadPoint(("event", 100), 1.5, 1, "button", Fraction(3), "2")
        [assigned] = s._assign_points_to_onsets([cluster], [late])
        self.assertEqual(len(assigned), 1)
        filtered = s._filter_tricky_cluster(cluster, 0.25)
        self.assertEqual(s._assign_points_to_onsets([filtered], [late]), [[]])

    def test_default_filter_preserves_sequence_and_canonical_events(self):
        parsed = parse_chart(
            "(120){4}1-5[4:1],2-6[4:1],3-7[4:1],"
            "4-8[3##0.2],2,4,6,8,2,4,6,E"
        )
        snapshot = deepcopy(parsed.events)
        duration = max(parsed.chart_end_time_s, parsed.last_event_end_s or 0)
        filtered = s.slide_feature_breakdown(tuple(parsed.events), duration)
        with patch.object(s, "_filter_tricky_cluster", side_effect=lambda cluster, mode: cluster):
            original = s.slide_feature_breakdown(tuple(parsed.events), duration)
        self.assertEqual(len(original.tricky_points), 4)
        self.assertEqual(len(filtered.tricky_points), 3)
        self.assertLess(filtered.tricky, original.tricky)
        self.assertEqual(filtered.sequence, original.sequence)
        self.assertEqual(filtered.sections, original.sections)
        self.assertEqual(parsed.events, snapshot)

    def test_uniform_slow_waits_are_kept(self):
        parsed = parse_chart("(27.5){4}1-5[4:1],2-6[4:1],3-7[4:1],E")
        result = s.slide_feature_breakdown(tuple(parsed.events), parsed.last_event_end_s)
        self.assertEqual(len(result.tricky_points), 3)


if __name__ == "__main__":
    unittest.main()
