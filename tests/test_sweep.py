"""Variable-width sweep-family recognition and numeric aggregation."""

from dataclasses import replace
from fractions import Fraction
import math
import unittest

from mairadar.analysis import AnalysisContext
from mairadar.analysis.features import SweepAnalyzer
from mairadar.analysis.features.hand_motion import sweep_family_hand_motion
from mairadar.analysis.features.sweep import (
    ButtonAttack,
    SweepScoringConfig,
    SweepSequence,
    button_attacks,
    score_sweep_sequences,
    sweep_score,
    sweep_sequences,
)
from mairadar.model import Event
from mairadar.parser import parse_chart


def events(text):
    return tuple(parse_chart(text).events)


PERMISSIVE_CONFIG = SweepScoringConfig(
    chord_note_multiplier=1.0,
    minimum_family_attack_count=3,
)


class SweepTests(unittest.TestCase):
    def test_half_percent_speed_tolerance_ignores_conversion_jitter(self):
        times = (0.0, 1 / 12, 1 / 12 + (1 / 12) * 1.004, 3 / 12 + (1 / 12) * 0.004)
        chart = tuple(
            Event(
                event_id=index,
                kind="tap",
                start_time_s=time_s,
                end_time_s=time_s,
                start_beat=str(Fraction(index - 1, 4)),
                end_beat=str(Fraction(index - 1, 4)),
                position=str(index),
                is_slide_head=False,
                is_break=False,
                is_ex=False,
                is_mine=False,
                flags_json={},
            )
            for index, time_s in enumerate(times, start=1)
        )
        [tolerant] = sweep_sequences(chart)
        [strict] = sweep_sequences(chart, speed_relative_tolerance=0)
        self.assertFalse(tolerant.speed_switch_indexes)
        self.assertTrue(strict.speed_switch_indexes)

    def test_reference_speed_is_180_bpm_sixteenths_with_sqrt_scaling(self):
        chart = events("(180){16}1,2,3,E")
        [sequence] = sweep_sequences(chart)
        self.assertIsInstance(sequence, SweepSequence)
        self.assertEqual(sequence.lanes_by_batch, ((1,), (2,), (3,)))
        self.assertEqual(sequence.unit_intervals_beats, (Fraction(1, 4),) * 2)
        self.assertAlmostEqual(sequence.interval_seconds, 1 / 12)
        self.assertAlmostEqual(
            sweep_score(chart, config=PERMISSIVE_CONFIG), 18
        )
        self.assertAlmostEqual(
            sweep_score(events("(90){16}1,2,3,E"), config=PERMISSIVE_CONFIG),
            9 * math.sqrt(0.5),
        )

    def test_full_two_hand_loop_is_one_double_sweep(self):
        [sequence] = sweep_sequences(events(
            "(180){16}73,84,15,26,37,48,51,26,37,E"
        ))
        self.assertEqual(sequence.widths, (2,) * 9)
        self.assertEqual(sequence.attack_count, 18)
        self.assertEqual(len(sequence.strands), 1)
        self.assertIn(
            sequence.strands[0].lanes,
            {
                (3, 4, 5, 6, 7, 8, 1, 2, 3),
                (7, 8, 1, 2, 3, 4, 5, 6, 7),
            },
        )

    def test_single_sweep_can_expand_into_opposed_double_sweep(self):
        [sequence] = sweep_sequences(events(
            "(180){16}5,6,7,8,1,27,36,45,E"
        ))
        self.assertEqual(sequence.widths, (1, 1, 1, 1, 1, 2, 2, 2))
        self.assertEqual(sequence.width_switch_indexes, (5,))
        self.assertEqual(sequence.attack_count, 11)
        self.assertEqual(len(sequence.strands), 1)
        self.assertEqual(sequence.strands[0].lanes, (5, 6, 7, 8, 1, 2, 3, 4))

    def test_nested_single_double_fold_remains_one_family(self):
        [sequence] = sweep_sequences(events(
            "(180){16}1,28,37,46,5,46,37,28,1,E"
        ))
        self.assertEqual(sequence.widths, (1, 2, 2, 2, 1, 2, 2, 2, 1))
        self.assertEqual(sequence.attack_count, 15)
        self.assertEqual(sequence.width_switch_indexes, (1, 4, 5, 8))
        self.assertEqual(sequence.start_beat, 0)
        self.assertEqual(sequence.end_beat, 2)

    def test_previous_single_double_transition_examples_are_not_split(self):
        expanded = sweep_sequences(events("(180){16}1,2,3,4,51,26,37,E"))
        contracted = sweep_sequences(events("(180){16}18,27,36,45,6,7,8,E"))
        self.assertEqual(len(expanded), 1)
        self.assertEqual(expanded[0].widths, (1, 1, 1, 1, 2, 2, 2))
        self.assertEqual(len(contracted), 1)
        self.assertEqual(contracted[0].widths, (2, 2, 2, 2, 1, 1, 1))
        self.assertEqual(contracted[0].double_handoff_indexes, (3,))
        self.assertFalse(contracted[0].direction_switch_indexes)

    def test_stable_internal_chord_notes_are_attached_but_not_used_for_speed(self):
        [sequence] = sweep_sequences(events("(180){16}1,2,3/7,4,5,E"))
        self.assertEqual(sequence.lanes_by_batch, ((1,), (2,), (3, 7), (4,), (5,)))
        self.assertEqual(sequence.attack_count, 6)
        self.assertEqual([len(ids) for ids in sequence.extra_event_ids_by_batch], [0, 0, 1, 0, 0])
        self.assertEqual(sequence.interval_seconds, 1 / 12)
        self.assertAlmostEqual(
            sweep_score(
                events("(180){16}1,2,3/7,4,5,E"),
                config=PERMISSIVE_CONFIG,
            ),
            18,
        )

    def test_chord_can_switch_from_incoming_to_outgoing_main_spine(self):
        [sequence] = sweep_sequences(events("(180){16}3,4,56,7,8,E"))
        self.assertEqual(
            sequence.lanes_by_batch,
            ((3,), (4,), (5, 6), (7,), (8,)),
        )
        self.assertEqual(sequence.attack_count, 6)
        self.assertEqual(sequence.double_handoff_indexes, (2,))
        self.assertEqual(sequence.extra_event_ids_by_batch[2], ())
        self.assertFalse(sequence.direction_switch_indexes)

    def test_chord_handoff_does_not_turn_plain_alternation_into_a_sweep(self):
        self.assertFalse(sweep_sequences(events("(180){16}2,3,2,3,E")))

    def test_opt_in_paired_sweeps_include_distant_two_note_chunks(self):
        xaleid = events("(180){32}2,3,6,5,2,3,6,5,E")
        aegleseeker = events("(180){16}1,2,8,7,1,2,8,7,E")
        rotating = events("(180){16}2,3,8,1,6,7,4,5,2,3,8,1,6,7,E")
        self.assertFalse(sweep_sequences(xaleid))
        self.assertFalse(sweep_sequences(aegleseeker))
        for chart, expected in ((xaleid, 8), (aegleseeker, 8), (rotating, 14)):
            with self.subTest(expected=expected):
                paired = [
                    sequence for sequence in sweep_sequences(
                        chart, include_paired_sweeps=True
                    )
                    if sequence.paired_sweep
                ]
                self.assertEqual(sum(seq.attack_count for seq in paired), expected)
        self.assertFalse(sweep_sequences(
            events("(180){16}2,3,2,3,2,3,2,3,E"),
            include_paired_sweeps=True,
        ))
        self.assertFalse(sweep_sequences(
            events("(180){8}2,3,6,5,2,3,6,5,E"),
            include_paired_sweeps=True,
        ))
        gradual = events(
            "(180){32}2,3,6,5,2,3,7,6,3,4,7,6,3,4,8,7,E"
        )
        self.assertEqual(
            sum(sequence.attack_count for sequence in sweep_sequences(
                gradual, include_paired_sweeps=True,
            ) if sequence.paired_sweep),
            16,
        )

    def test_strict_paired_sweeps_need_opposite_direction_and_feasible_hands(self):
        positive = events(
            "(180){32}2,3,6,5,2,3,7,6,3,4,7,6,3,4,8,7,E"
        )
        strict = sweep_sequences(
            positive,
            include_paired_sweeps=True,
            strict_opposite_pairs=True,
            paired_max_interval_seconds=1 / 12,
        )
        self.assertEqual(sum(s.attack_count for s in strict if s.paired_sweep), 16)

        same_direction = events(
            "(168){16}2,3,8,1,6,7,4,5,2,3,8,1,6,7,E"
        )
        self.assertFalse(any(s.paired_sweep for s in sweep_sequences(
            same_direction,
            include_paired_sweeps=True,
            strict_opposite_pairs=True,
        )))

        scattered = events(
            "(163){16}4,5,2,1,3,4,1,8,4,5,8,7,5,6,E"
        )
        # Hand feasibility alone cannot disambiguate this pattern.
        self.assertTrue(any(s.paired_sweep for s in sweep_sequences(
            scattered,
            include_paired_sweeps=True,
            strict_opposite_pairs=True,
        )))
        self.assertFalse(any(s.paired_sweep for s in sweep_sequences(
            scattered,
            include_paired_sweeps=True,
            strict_opposite_pairs=True,
            paired_max_interval_seconds=1 / 12,
        )))

    def test_opt_in_repeated_double_front_bridges_exact_eighth_gap(self):
        chart = events("(168){48}18,27,36,45,,,,,,18,27,36,45,E")
        sequences = sweep_sequences(chart)
        self.assertEqual(len(sequences), 2)
        plain = score_sweep_sequences(sequences, config=PERMISSIVE_CONFIG)
        bridged = score_sweep_sequences(
            sequences,
            config=replace(PERMISSIVE_CONFIG, eighth_gap_family_bridge=True),
        )
        self.assertEqual(len(plain.families), 2)
        self.assertEqual(len(bridged.families), 1)
        self.assertEqual(bridged.groups[1].parent_group_id, 1)
        motion = sweep_family_hand_motion(
            bridged.families[0], bridged.groups, respect_group_gaps=True,
        )
        self.assertGreater(motion.idle_reposition_distance, 0)
        self.assertEqual(motion.fast_jump_violations, 0)
        wider_gap = sweep_sequences(events(
            "(168){48}18,27,36,45,,,,,,,18,27,36,45,E"
        ))
        self.assertEqual(len(score_sweep_sequences(
            wider_gap,
            config=replace(PERMISSIVE_CONFIG, eighth_gap_family_bridge=True),
        ).families), 2)

    def test_opt_in_eighth_bridge_accepts_different_shapes_at_similar_speed(self):
        chart = events(
            "(168){48}18,27,36,45,,,,,,28,37,46,51,E"
        )
        sequences = sweep_sequences(chart)
        self.assertEqual(len(sequences), 2)
        self.assertNotEqual(
            sequences[0].lanes_by_batch,
            sequences[1].lanes_by_batch,
        )
        baseline = score_sweep_sequences(sequences, config=PERMISSIVE_CONFIG)
        bridged = score_sweep_sequences(
            sequences,
            config=replace(
                PERMISSIVE_CONFIG,
                eighth_gap_similar_speed_bridge=True,
            ),
        )
        self.assertEqual(len(baseline.families), 2)
        self.assertEqual(len(bridged.families), 1)
        self.assertEqual(bridged.families[0].attack_count, 16)
        self.assertAlmostEqual(
            bridged.families[0].density,
            bridged.families[0].load / bridged.families[0].duration_s,
        )

        slower = sweep_sequences(events(
            "(168){48}18,27,36,45,,,,,,{24}28,37,46,51,E"
        ))
        self.assertEqual(len(score_sweep_sequences(
            slower,
            config=replace(
                PERMISSIVE_CONFIG,
                eighth_gap_similar_speed_bridge=True,
            ),
        ).families), 2)

    def test_short_hold_and_slide_head_are_attacks_but_long_hold_is_occupancy(self):
        chart = events("(180){16}1,2h[16:1],3-5[4:1],4h[4:1],E")
        attacks = button_attacks(chart)
        self.assertTrue(all(isinstance(attack, ButtonAttack) for attack in attacks))
        self.assertEqual([attack.lane for attack in attacks], [1, 2, 3])
        self.assertEqual([attack.kinds for attack in attacks], [
            ("tap",), ("hold",), ("slide_head",),
        ])
        self.assertEqual(sweep_sequences(chart)[0].lanes_by_batch, ((1,), (2,), (3,)))

    def test_long_hold_can_bridge_exactly_two_lanes_at_twice_the_interval(self):
        bridged = events("(180){16}1/2h[4:1],,3,4,E")
        unoccupied = events("(180){16}1,,3,4,E")
        [sequence] = sweep_sequences(bridged)
        self.assertEqual(sequence.lanes_by_batch, ((1,), (3,), (4,)))
        self.assertEqual(sequence.unit_intervals_beats, (Fraction(1, 4), Fraction(1, 4)))
        self.assertFalse(sweep_sequences(unoccupied))

    def test_duplicate_and_ex_declarations_keep_individual_weights(self):
        chart = events("(180){16}1/1x,2x,3,E")
        attacks = button_attacks(chart)
        self.assertEqual(len(attacks), 3)
        self.assertEqual(
            (attacks[0].normal_declaration_count, attacks[0].protected_declaration_count),
            (1, 1),
        )
        [sequence] = sweep_sequences(chart)
        self.assertEqual(sequence.attack_count, 3)
        self.assertEqual(sequence.declaration_count, 4)
        self.assertAlmostEqual(
            sweep_score(chart, config=PERMISSIVE_CONFIG),
            (1 + 0.3 + 0.3 + 1) * 6,
        )

    def test_twelve_note_gate_and_deceleration_only_eighth_exception(self):
        self.assertTrue(sweep_sequences(events("(180){12}1,2,3,E")))
        self.assertFalse(sweep_sequences(events("(180){8}1,2,3,E")))
        self.assertFalse(sweep_sequences(events("(180){4}1,2,3,E")))
        decelerating = sweep_sequences(events(
            "(180){16}1,2,3,4,{8}5,6,7,E"
        ))
        self.assertEqual(len(decelerating), 1)
        self.assertEqual(decelerating[0].speed_switch_indexes, (5,))
        self.assertEqual(decelerating[0].unit_intervals_beats[-2:], (Fraction(1, 2),) * 2)

    def test_direction_reversal_weights_the_pivot_with_the_following_run(self):
        chart = events("(180){16}1,2,3,4,5,4,3,2,E")
        [sequence] = sweep_sequences(chart)
        self.assertEqual(sequence.direction_switch_indexes, (4,))
        # Four attacks at 1.0, then pivot and four-note reverse section at 1.2.
        self.assertAlmostEqual(
            sweep_score(chart, config=PERMISSIVE_CONFIG),
            (4 + 4 * 1.2) / (7 / 12),
        )

    def test_directionless_single_connection_stays_in_family_without_bonus(self):
        chart = events("(180){16}1,2,3,8,7,6,E")
        sequences = sweep_sequences(chart)
        self.assertEqual(len(sequences), 2)
        result = score_sweep_sequences(sequences, config=PERMISSIVE_CONFIG)
        self.assertEqual(result.groups[1].parent_group_id, 1)
        self.assertEqual(result.groups[1].connection_increment, 0)
        self.assertEqual(result.groups[1].family_multiplier, 1)
        self.assertAlmostEqual(result.value, 6 / (5 / 12))

    def test_near_start_same_direction_single_connection_gets_point_three(self):
        chart = events("(180){16}1,2,3,4,2,3,4,E")
        result = score_sweep_sequences(
            sweep_sequences(chart), config=PERMISSIVE_CONFIG
        )
        self.assertAlmostEqual(result.groups[1].connection_increment, 0.3)
        self.assertAlmostEqual(result.groups[1].family_multiplier, 1.3)

    def test_double_handoff_keeps_family_without_bonus(self):
        parent = sweep_sequences(events("(180){16}1,2,3,E"))[0]
        child = sweep_sequences(events("(180){16}5,6,7,E"))[0]
        child = replace(
            child,
            times_s=tuple(time_s + parent.end_time_s for time_s in child.times_s),
        )
        result = score_sweep_sequences(
            (parent, child), config=PERMISSIVE_CONFIG
        )
        self.assertEqual(result.groups[1].connection_increment, 0)
        self.assertEqual(result.groups[1].family_multiplier, 1)

    def test_only_top_five_family_densities_use_inverse_sqrt_rank(self):
        body = ",,,,,".join(("1,2,3", "5,6,7") * 3)
        sequences = sweep_sequences(events(f"(180){{16}}{body},E"))
        result = score_sweep_sequences(sequences, config=PERMISSIVE_CONFIG)
        self.assertEqual(len(result.families), 6)
        self.assertEqual(sum(family.discount > 0 for family in result.families), 5)
        self.assertAlmostEqual(
            result.value,
            18 * sum(rank ** -0.5 for rank in range(1, 6)),
        )

    def test_pseudo_each_uses_parser_timing_and_zero_duration_is_unavailable(self):
        [sequence] = sweep_sequences(events("(120){4}1`2`3,E"))
        self.assertEqual(sequence.unit_intervals_beats, (Fraction(1, 32),) * 2)
        zeroth = AnalysisContext((), chart_end_time_s=0, last_event_end_s=None)
        self.assertFalse(SweepAnalyzer().analyze(zeroth).success)

    def test_analyzer_blends_all_family_mean_with_eligible_top_five_peak(self):
        short = parse_chart("(180){16}1,2,3,E")
        short_context = AnalysisContext(
            tuple(short.events), short.chart_end_time_s, short.last_event_end_s,
        )
        short_details = score_sweep_sequences(
            sweep_sequences(tuple(short.events)),
            duration_s=short_context.duration_s,
        )
        self.assertEqual(short_details.family_mean, 18)
        self.assertAlmostEqual(
            short_details.duration_factor,
            math.sqrt(short_context.duration_s / 150),
        )
        self.assertAlmostEqual(
            SweepAnalyzer().analyze(short_context).data,
            0.6 * 18 * math.sqrt(short_context.duration_s / 150),
        )

        parsed = parse_chart("(150){16}1,2,3,4,5,6,7,8,1,E")
        context = AnalysisContext(
            tuple(parsed.events), parsed.chart_end_time_s, parsed.last_event_end_s,
        )
        stretched = replace(context, chart_end_time_s=context.chart_end_time_s + 100)
        original = SweepAnalyzer().analyze(context).data
        extended = SweepAnalyzer().analyze(stretched).data
        self.assertGreater(extended, original)
        self.assertGreater(extended, 0)

        details = score_sweep_sequences(
            sweep_sequences(tuple(parsed.events)),
            duration_s=context.duration_s,
        )
        self.assertAlmostEqual(
            details.value,
            0.6 * details.mean_load + 0.4 * details.peak_score,
        )

    def test_chord_objects_receive_local_one_point_three_multiplier(self):
        chart = events("(180){16}1,2/6,3,4,E")
        [sequence] = sweep_sequences(chart)
        plain = replace(PERMISSIVE_CONFIG, chord_note_multiplier=1.0)
        boosted = replace(PERMISSIVE_CONFIG, chord_note_multiplier=1.3)
        self.assertAlmostEqual(score_sweep_sequences((sequence,), config=plain).value, 20)
        self.assertAlmostEqual(score_sweep_sequences((sequence,), config=boosted).value, 22.4)

    def test_default_family_threshold_is_inclusive_at_nine_attacks(self):
        eight = events("(120){12}1,2,3,4,5,6,7,8,E")
        nine = events("(180){16}1,2,3,4,5,6,7,8,1,E")
        self.assertEqual(sweep_score(eight), 0)
        self.assertGreater(sweep_score(nine), 0)

    def test_configuration_and_resource_limits_validate(self):
        for kwargs in (
            {"max_states": 0},
            {"reference_interval_seconds": 0},
            {"speed_exponent": True},
            {"protected_note_weight": float("inf")},
            {"mean_weight": 0.7},
            {"duration_reference_seconds": 0},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                SweepAnalyzer(**kwargs)
        with self.assertRaises(ValueError):
            SweepScoringConfig(single_connection_increment=-1).validate()
        with self.assertRaises(ValueError):
            sweep_sequences(events("(180){16}1,2,3,E"), max_states=1)


if __name__ == "__main__":
    unittest.main()
