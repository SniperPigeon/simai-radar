"""Same-button sequence extraction and geometric Top-K aggregation."""

import unittest
from fractions import Fraction

from mairadar.analysis import AnalysisContext
from mairadar.analysis.features import JackSequenceAnalyzer
from mairadar.analysis.features.jack import JackSequence, jack_score, jack_sequences
from mairadar.model import Event
from mairadar.parser import parse_chart


def events(text):
    return tuple(parse_chart(text).events)


def button(event_id, beat, position, kind="tap", time_s=None):
    beat = Fraction(beat)
    return Event(
        event_id=event_id,
        kind=kind,
        start_time_s=float(beat) / 2 if time_s is None else time_s,
        start_beat=str(beat),
        position=str(position),
    )


class JackSequenceTests(unittest.TestCase):
    def test_pure_tap_and_hold_sequence_counts_both_kinds(self):
        sequences = jack_sequences(events("(120){8}1,1h[4:1],1,E"))
        [sequence] = sequences
        self.assertIsInstance(sequence, JackSequence)
        self.assertEqual(sequence.position, "1")
        self.assertEqual((sequence.start_beat, sequence.end_beat), (0, 1))
        self.assertEqual((sequence.anchor_count, sequence.interrupting_tap_count), (3, 0))
        self.assertEqual(sequence.interrupting_span_beats, 0)
        self.assertEqual(sequence.equivalent_eighth_bpm, 120)
        self.assertAlmostEqual(sequence.speed_factor, (120 / 180) ** 1.5)
        self.assertAlmostEqual(
            jack_score(
                events("(120){8}1,1h[4:1],1,E"),
                speed_reference_eighth_bpm=120,
            ),
            3.9,
        )

    def test_short_off_button_tap_after_two_main_notes_receives_weight(self):
        chart = events("(120){16}1,1,2,1,E")
        [sequence] = jack_sequences(chart)
        self.assertEqual(sequence.position, "1")
        self.assertEqual(sequence.anchor_count, 3)
        self.assertEqual(sequence.interrupting_tap_count, 1)
        self.assertEqual(sequence.interrupting_span_beats, Fraction(1, 4))
        self.assertEqual(sequence.strength, 4.5)

    def test_four_interruptions_fit_across_a_long_jack(self):
        chart = events(
            "(120){32}1,1,2,1,1,3,1,1,4,1,1,5,1,E"
        )
        [sequence] = jack_sequences(chart)
        self.assertEqual((sequence.anchor_count, sequence.interrupting_tap_count), (9, 4))
        self.assertEqual(sequence.interrupting_span_beats, Fraction(1, 2))
        self.assertEqual(sequence.strength, 15)

    def test_one_flight_can_contain_four_off_button_timestamps(self):
        chart = events("(120){32}1,1,2,3,4,5,1,E")
        [sequence] = jack_sequences(chart)
        self.assertEqual(sequence.anchor_count, 3)
        self.assertEqual(sequence.interrupting_tap_count, 4)
        self.assertEqual(sequence.interrupting_span_beats, Fraction(1, 2))

    def test_interruption_count_cap_is_per_flight_not_cumulative(self):
        chart = events(
            "(120){64}1,1,2,1,1,3,1,1,4,1,1,5,1,1,6,1,E"
        )
        [sequence] = jack_sequences(chart)
        self.assertEqual(sequence.interrupting_tap_count, 5)
        self.assertEqual(sequence.end_beat, Fraction(15, 16))

        too_many_at_once = events("(120){64}1,1,2/3/4/5/6,1,E")
        [prefix] = jack_sequences(too_many_at_once)
        self.assertEqual(prefix.anchor_count, 2)
        self.assertEqual(prefix.interrupting_tap_count, 0)

    def test_each_flight_gets_its_own_eighth_note_time_budget(self):
        chart = events("(120){8}1,1,2,1,1,3,1,E")
        [sequence] = jack_sequences(chart)
        self.assertEqual(sequence.interrupting_tap_count, 2)
        self.assertEqual(sequence.interrupting_span_beats, 1)
        self.assertEqual(sequence.end_beat, 3)

    def test_single_flight_longer_than_an_eighth_splits_sequence(self):
        chart = events("(120){16}1,1,2,3,4,1,E")
        [prefix] = jack_sequences(chart)
        self.assertEqual(prefix.anchor_count, 2)
        self.assertEqual(prefix.interrupting_tap_count, 0)
        self.assertEqual(prefix.end_beat, Fraction(1, 4))

    def test_interaction_before_two_main_notes_does_not_join_the_jack(self):
        chart = events("(120){16}1,2,1,1,E")
        [sequence] = jack_sequences(chart)
        self.assertEqual(sequence.anchor_count, 2)
        self.assertEqual(sequence.start_beat, Fraction(1, 2))
        self.assertEqual(sequence.interrupting_tap_count, 0)

    def test_two_main_onsets_are_required_between_flights(self):
        chart = events("(120){32}1,1,2,1,3,1,E")
        sequences = jack_sequences(chart)
        self.assertEqual(sequences[0].anchor_count, 3)
        self.assertEqual(sequences[0].interrupting_tap_count, 1)

    def test_eighth_note_boundary_is_inclusive_and_slower_gap_splits(self):
        self.assertAlmostEqual(
            jack_score(events("(120){8}1,1,E"), speed_reference_eighth_bpm=120),
            2.6,
        )
        self.assertEqual(jack_score(events("(120){4}1,1,E")), 0)

    def test_off_button_hold_ends_candidate_but_touch_and_slide_do_not_enter_stream(self):
        self.assertEqual(
            jack_score(events("(120){16}1,2h[4:1],1,E")),
            0,
        )
        self.assertEqual(
            jack_score(
                events("(120){16}1,A1,1,E"),
                speed_reference_eighth_bpm=120,
            ),
            2.6,
        )

    def test_simultaneous_duplicates_are_counted_but_need_two_onset_times(self):
        self.assertEqual(jack_score(events("(120){16}1/1,E")), 0)
        self.assertAlmostEqual(
            jack_score(
                events("(120){16}1/1,1,E"),
                speed_reference_eighth_bpm=240,
            ),
            3.9,
        )

    def test_top_k_uses_one_based_geometric_rank_decay(self):
        chart = (
            button(1, 0, 1), button(2, "1/4", 1), button(3, "1/2", 1),
            button(4, 2, 2), button(5, "9/4", 2),
            button(6, 4, 3), button(7, "17/4", 3),
        )
        self.assertAlmostEqual(
            jack_score(
                chart,
                top_k=2,
                max_interrupting_taps=0,
                speed_reference_eighth_bpm=240,
            ),
            1.3 * 3 + 1.3 * 0.645 * 2,
        )

    def test_speed_weighted_strength_determines_rank_before_decay(self):
        chart = (
            button(1, 0, 1, time_s=0.0),
            button(2, "1/2", 1, time_s=1 / 6),
            button(3, 1, 1, time_s=1 / 3),
            button(4, 2, 2, time_s=1.0),
            button(5, "9/4", 2, time_s=25 / 24),
        )
        sequences = jack_sequences(chart)
        self.assertEqual([sequence.position for sequence in sequences], ["2", "1"])
        self.assertGreater(sequences[0].weighted_strength, sequences[1].weighted_strength)

    def test_actual_main_button_speed_weights_sequence_strength(self):
        eighth = events("(120){8}1,1,1,E")
        sixteenth = events("(120){16}1,1,1,E")
        self.assertAlmostEqual(jack_score(eighth), 1.3 * 3 * (120 / 180) ** 1.5)
        self.assertAlmostEqual(jack_score(sixteenth), 1.3 * 3 * (240 / 180) ** 1.5)
        self.assertGreater(jack_score(sixteenth), jack_score(eighth))

    def test_top_one_has_about_forty_percent_of_equal_sequence_rank_weight(self):
        weights = [
            1.3 * 0.645 ** index
            for index in range(5)
        ]
        self.assertAlmostEqual(weights[0] / sum(weights), 0.4, places=3)

    def test_analyzer_zero_and_invalid_duration_contract(self):
        analyzer = JackSequenceAnalyzer()
        empty = AnalysisContext((), chart_end_time_s=1.0, last_event_end_s=None)
        zeroth = AnalysisContext((), chart_end_time_s=0.0, last_event_end_s=None)
        self.assertEqual(analyzer.analyze(empty).data, 0)
        self.assertFalse(analyzer.analyze(zeroth).success)

    def test_parameters_are_explicitly_validated(self):
        for top_k in (0, -1, 1.5, True):
            with self.subTest(top_k=top_k), self.assertRaises(ValueError):
                JackSequenceAnalyzer(top_k=top_k)
        for interruptions in (-1, 1.5, True):
            with self.subTest(interruptions=interruptions), self.assertRaises(ValueError):
                JackSequenceAnalyzer(max_interrupting_taps=interruptions)
        for speed in (0, -1, float("inf"), True):
            with self.subTest(speed=speed), self.assertRaises(ValueError):
                JackSequenceAnalyzer(speed_reference_eighth_bpm=speed)
        for exponent in (0, -1, float("nan"), True):
            with self.subTest(exponent=exponent), self.assertRaises(ValueError):
                JackSequenceAnalyzer(speed_exponent=exponent)


if __name__ == "__main__":
    unittest.main()
