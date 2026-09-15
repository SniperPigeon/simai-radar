"""Maximal sweep recognition and numeric aggregation."""

from fractions import Fraction
import unittest

from mairadar.analysis import AnalysisContext
from mairadar.analysis.features import SweepAnalyzer
from mairadar.analysis.features.sweep import (
    ButtonAttack,
    SweepScoringConfig,
    SweepSequence,
    button_attacks,
    score_sweep_sequences,
    sweep_score,
    sweep_sequences,
)
from mairadar.parser import parse_chart


def events(text):
    return tuple(parse_chart(text).events)


def sequence(group_id, lanes, start_s, end_s):
    return SweepSequence(
        lanes=tuple(lanes),
        event_ids_by_attack=tuple(
            (group_id * 10 + index,) for index in range(len(lanes))
        ),
        start_beat=Fraction(str(start_s)),
        end_beat=Fraction(str(end_s)),
        start_time_s=start_s,
        end_time_s=end_s,
        gaps_beats=tuple(Fraction(1) for _ in range(len(lanes) - 1)),
        period_beats=Fraction(1),
        turn_note_indexes=(),
    )


class SweepTests(unittest.TestCase):
    def test_three_adjacent_sixteenths_use_point_one_second_reference(self):
        chart = events("(180){16}1,2,3,E")
        [sequence] = sweep_sequences(chart)
        self.assertIsInstance(sequence, SweepSequence)
        self.assertEqual(sequence.lanes, (1, 2, 3))
        self.assertEqual(sequence.gaps_beats, (Fraction(1, 4), Fraction(1, 4)))
        self.assertAlmostEqual(sequence.interval_seconds, 1 / 12)
        self.assertAlmostEqual(sweep_score(chart), 3.6)

    def test_hold_and_slide_head_are_attacks_but_headless_slide_is_not(self):
        chart = events("(180){16}1,2h[4:1],3-5[4:1],4?-6[4:1],E")
        attacks = button_attacks(chart)
        self.assertTrue(all(isinstance(attack, ButtonAttack) for attack in attacks))
        self.assertEqual([attack.lane for attack in attacks], [1, 2, 3])
        self.assertEqual([attack.kinds for attack in attacks], [
            ("tap",), ("hold",), ("slide_head",),
        ])
        self.assertEqual(sweep_sequences(chart)[0].lanes, (1, 2, 3))

    def test_duplicate_declarations_share_an_attack_but_keep_their_weight(self):
        chart = events("(180){16}1/1,2,3,E")
        attacks = button_attacks(chart)
        self.assertEqual(len(attacks), 3)
        self.assertEqual(len(attacks[0].event_ids), 2)
        [sequence] = sweep_sequences(chart)
        self.assertEqual(sequence.attack_count, 3)
        self.assertEqual(sequence.declaration_count, 4)
        self.assertEqual(sequence.strength, 3)
        self.assertAlmostEqual(sweep_score(chart), 3.6)

    def test_uses_parser_pseudo_each_beats_instead_of_rebuilding_timing(self):
        chart = events("(120){4}1`2`3,E")
        [sequence] = sweep_sequences(chart)
        self.assertEqual(sequence.gaps_beats, (Fraction(1, 32), Fraction(1, 32)))
        self.assertEqual(sequence.period_beats, Fraction(1, 32))

    def test_one_intervening_attack_is_allowed_but_two_stop_the_link(self):
        one = sweep_sequences(events("(180){16}1,8,2,,3,E"))
        two = sweep_sequences(events("(180){16}1,7,8,2,,,3,E"))
        self.assertTrue(any(sequence.lanes == (1, 2, 3) for sequence in one))
        self.assertFalse(any(sequence.lanes == (1, 2, 3) for sequence in two))

    def test_direction_reversal_requires_two_steps_on_each_side(self):
        [sequence] = sweep_sequences(events("(180){16}1,2,3,2,1,E"))
        self.assertEqual(sequence.lanes, (1, 2, 3, 2, 1))
        self.assertEqual(sequence.turn_note_indexes, (2,))
        incomplete_turn = sweep_sequences(events("(180){16}1,2,3,2,E"))
        self.assertEqual(incomplete_turn[0].lanes, (1, 2, 3))

    def test_no_sweep_is_zero_and_zero_duration_is_unavailable(self):
        analyzer = SweepAnalyzer()
        context = AnalysisContext(
            events("(180){16}1,3,5,E"),
            chart_end_time_s=1,
            last_event_end_s=0.5,
        )
        self.assertEqual(analyzer.analyze(context).data, 0)
        zeroth = AnalysisContext((), chart_end_time_s=0, last_event_end_s=None)
        self.assertFalse(analyzer.analyze(zeroth).success)

    def test_faster_sweep_has_larger_numeric_value(self):
        slow = sweep_score(events("(90){16}1,2,3,E"))
        reference = sweep_score(events("(180){16}1,2,3,E"))
        self.assertAlmostEqual(slow, 1.8)
        self.assertGreater(reference, slow)

    def test_continuation_bonus_rank_discount_and_duration_normalization(self):
        first = sequence(1, (1, 2, 3), 0.0, 0.2)
        second = sequence(2, (1, 8, 7), 0.2, 0.4)
        result = score_sweep_sequences((first, second), duration_s=2.0)
        self.assertEqual(result.groups[0].continuation_multiplier, 8)
        self.assertEqual(result.groups[0].follower_group_id, 2)
        self.assertEqual(result.groups[0].boosted_weight, 24)
        self.assertEqual(result.groups[1].count_discount, 2 ** -0.5)
        expected_total = 24 + 3 * 2 ** -0.5
        self.assertAlmostEqual(result.weighted_total, expected_total)
        self.assertAlmostEqual(result.value, expected_total / 2)

    def test_simultaneous_groups_use_one_point_six_without_multiplying(self):
        first = sequence(1, (1, 2, 3), 0.0, 0.2)
        second = sequence(2, (5, 6, 7), 0.0, 0.2)
        result = score_sweep_sequences((first, second))
        self.assertEqual(result.groups[0].simultaneous_group_ids, (2,))
        self.assertEqual(result.groups[1].simultaneous_group_ids, (1,))
        self.assertTrue(all(group.total_multiplier == 1.6 for group in result.groups))
        self.assertAlmostEqual(result.value, 4.8 + 4.8 / 2 ** 0.5)

    def test_continuation_gap_multiplier_boundaries(self):
        current = sequence(1, (1, 2, 3), 0.0, 0.2)
        for follower_start, expected in ((0.35, 1.0), (0.4, 1.2), (0.6, 1.2)):
            with self.subTest(follower_start=follower_start):
                follower = sequence(2, (4, 5, 6), follower_start, follower_start + 0.2)
                result = score_sweep_sequences((current, follower))
                self.assertEqual(result.groups[0].continuation_multiplier, expected)

    def test_analyzer_returns_per_second_intensity(self):
        parsed = parse_chart("(180){16}1,2,3,E")
        context = AnalysisContext(
            tuple(parsed.events), parsed.chart_end_time_s, parsed.last_event_end_s,
        )
        self.assertAlmostEqual(SweepAnalyzer().analyze(context).data, 14.4)

    def test_configuration_and_resource_limits_validate(self):
        for kwargs in (
            {"max_states": 0},
            {"reference_interval_seconds": 0},
            {"speed_exponent": True},
            {"count_discount_exponent": float("inf")},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                SweepAnalyzer(**kwargs)
        with self.assertRaises(ValueError):
            SweepScoringConfig(reference_interval_seconds=-1).validate()
        with self.assertRaises(ValueError):
            sweep_sequences(events("(180){16}1,2,3,E"), max_states=1)


if __name__ == "__main__":
    unittest.main()
