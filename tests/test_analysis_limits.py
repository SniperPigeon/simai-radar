"""Observable cancellation, work limits, and long-stream regressions."""

from fractions import Fraction
import unittest
from unittest.mock import Mock, patch

from mairadar.analysis import AnalysisCancelled, ChartAnalyzer, FeatureResult
from mairadar.analysis.features import SweepBurstAnalyzer
from mairadar.analysis.features.hand_motion import two_hand_motion
from mairadar.analysis.features.sweep import score_sweep_sequences, sweep_sequences
from mairadar.model import Event
from mairadar.parser import parse_chart
from mairadar.regression.derived import with_prediction
from mairadar.reporting import AnalysisRecord
from mairadar.scoring import FeatureScoreTransformer


class UnexpectedAnalyzer:
    def analyze(self, context):
        raise AssertionError("Feature must not be invoked")


class UnrequestedCancellationAnalyzer:
    def analyze(self, context):
        raise AnalysisCancelled("No caller cancellation was requested")


class CompletedAnalyzer:
    def analyze(self, context):
        return FeatureResult(3)


def buttons(count, *, independent=False):
    output = []
    for index in range(count):
        beat = Fraction(index // 3 * 8 + index % 3, 4) if independent else Fraction(index, 4)
        seconds = float(beat) / 3
        output.append(Event(
            event_id=index + 1, kind="tap", is_ex=False,
            position=str(index % 3 + 1 if independent else index % 8 + 1),
            start_beat=str(beat), end_beat=str(beat),
            start_time_s=seconds, end_time_s=seconds,
        ))
    return tuple(output)


class AnalysisLimitsTests(unittest.TestCase):
    def test_event_budget_fails_before_feature_work(self):
        analyzer = ChartAnalyzer({"unexpected": UnexpectedAnalyzer})
        with patch("mairadar.analysis.engine.MAXIMUM_CHART_EVENTS", 2):
            result = analyzer.analyze(parse_chart("(180){16}1,2,3,E"))
        self.assertEqual(result.status, "error")
        self.assertEqual([item.code for item in result.diagnostics], ["EVENT_BUDGET_EXCEEDED"])
        self.assertIsNone(result.features["unexpected"].data)

    def test_cancel_before_validation_and_during_sweep_skips_prediction(self):
        analyzer = ChartAnalyzer({"completed": CompletedAnalyzer, "sweep": SweepBurstAnalyzer,
                                  "later": UnexpectedAnalyzer})
        self.assertTrue(analyzer.analyze(None, is_cancelled=lambda: True).is_cancelled)
        checks = 0

        def is_cancelled():
            nonlocal checks
            checks += 1
            return checks >= 30

        parsed = parse_chart("(180){16}" + "1,2,3,4,5,6,7,8," * 20 + "E")
        result = analyzer.analyze(parsed, is_cancelled=is_cancelled)
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(result.features, {"completed": FeatureResult(3)})
        model = Mock(features=("sweep",))
        predicted = with_prediction(result, model)
        model.predict.assert_not_called()
        self.assertEqual(predicted.status, "cancelled")
        self.assertIsNone(predicted.features["fitted_constant"].data)
        mapper = Mock()
        scores = FeatureScoreTransformer({"completed": mapper}).transform(predicted)
        mapper.map.assert_not_called()
        self.assertEqual(scores.features, {})
        self.assertEqual(AnalysisRecord("cancelled", analysis=predicted, scores=scores).status,
                         "cancelled")

    def test_unrequested_cancellation_exception_is_a_feature_failure(self):
        analyzer = ChartAnalyzer({"broken": UnrequestedCancellationAnalyzer})
        result = analyzer.analyze(parse_chart("(180){16}1,2,3,E"))
        self.assertFalse(result.is_cancelled)
        self.assertEqual(result.status, "error")
        self.assertEqual(result.diagnostics[0].code, "FEATURE_FAILED")

    def test_sweep_budget_failure_keeps_other_features(self):
        with patch("mairadar.analysis.features.sweep.MAXIMUM_LIGHTWEIGHT_CANDIDATES", 1):
            result = ChartAnalyzer().analyze(parse_chart("(180){16}1,2,3,4,E"))
        self.assertEqual(result.status, "partial")
        self.assertFalse(result.features["sweep"].success)
        self.assertTrue(result.features["note"].success)
        self.assertIn("budget exceeded", result.diagnostics[0].message)

    def test_ten_thousand_note_sweep_preserves_the_complete_sequence(self):
        events = buttons(10_000)
        sequences = sweep_sequences(events)
        self.assertEqual(len(sequences), 1)
        self.assertEqual(sequences[0].event_ids_by_batch, tuple((e.event_id,) for e in events))
        motion = two_hand_motion(sequences[0].times_s, sequences[0].lanes_by_batch)
        self.assertEqual(len(motion.assignments), len(events))
        self.assertEqual(motion.total_distance, len(events) - 1)
        self.assertEqual(motion.free_hand_takeovers, 0)
        self.assertEqual(motion.fast_jump_violations, 0)

    def test_many_independent_sweeps_do_not_need_recursive_selection(self):
        sequences = sweep_sequences(buttons(3_600, independent=True))
        self.assertEqual(len(sequences), 1_200)
        scored = score_sweep_sequences(sequences)
        self.assertEqual(len(scored.families), len(sequences))
        self.assertTrue(all(len(family.group_ids) == 1 for family in scored.families))

    def test_hand_motion_can_cancel_inside_the_dynamic_program(self):
        calls = 0

        def check():
            nonlocal calls
            calls += 1
            if calls == 12:
                raise AnalysisCancelled()

        with self.assertRaises(AnalysisCancelled):
            two_hand_motion(tuple(i / 12 for i in range(40)),
                            tuple((i % 8 + 1,) for i in range(40)),
                            check_cancelled=check)
        self.assertEqual(calls, 12)
