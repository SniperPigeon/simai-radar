"""MVP metrics and independent in-memory analyzer dispatch."""

from copy import deepcopy
import subprocess
import sys
import unittest

from mairadar.analysis import ChartAnalyzer, FeatureResult
from mairadar.analysis.features import HoldFrequencyAnalyzer
from mairadar.parser import parse_chart


class FailingAnalyzer:
    def analyze(self, context):
        raise RuntimeError("feature deliberately failed")


class MutatingAnalyzer:
    def analyze(self, context):
        for event in context.events:
            event.kind = "tap"
        return FeatureResult(99.0)


class NonFiniteAnalyzer:
    def analyze(self, context):
        return FeatureResult(float("nan"))


class AnalysisTests(unittest.TestCase):
    def test_frequency_preserves_duplicates_rest_and_excludes_touch_hold(self):
        parsed = parse_chart("(120){4},1h[4:1]/1h[4:1]/Ch[4:1],,E")
        original = deepcopy(parsed)
        result = ChartAnalyzer().analyze(parsed)
        self.assertEqual(result.status, "ok")
        hold = result.features["hold"]
        self.assertAlmostEqual(hold.data, 2 / 1.5)
        self.assertTrue(hold.success)
        self.assertEqual(parsed, original)

    def test_long_hold_is_counted_once_and_extends_duration(self):
        result = ChartAnalyzer().analyze(parse_chart("(120){4}1h[1:2],E"))
        self.assertEqual(result.features["hold"].data, 0.25)

    def test_tempo_change_and_redundant_declaration(self):
        first = ChartAnalyzer().analyze(parse_chart("(120){4}1h[4:1],(240)2h[4:1],E"))
        repeated = ChartAnalyzer().analyze(parse_chart("(120)(120){4}1h[4:1],(240)(240)2h[4:1],E"))
        self.assertAlmostEqual(first.features["hold"].data, 2 / 0.75)
        self.assertEqual(first.features, repeated.features)

    def test_no_holds_is_zero_but_zero_time_is_unavailable(self):
        for text in ("(120){4}1,2,E", "(120){4},,E"):
            with self.subTest(text=text):
                result = ChartAnalyzer().analyze(parse_chart(text))
                self.assertEqual(result.features["hold"].data, 0)
                self.assertEqual(result.status, "ok")
        result = ChartAnalyzer().analyze(parse_chart("E"))
        self.assertIsNone(result.features["hold"].data)
        self.assertFalse(result.features["hold"].success)
        self.assertEqual(result.status, "error")

    def test_incomplete_and_invalid_inputs_do_not_produce_plausible_values(self):
        parsed = parse_chart("(120){4}1h[4:1],invalid,E")
        result = ChartAnalyzer().analyze(parsed)
        self.assertEqual(result.diagnostics[0].code, "PARSE_INCOMPLETE")
        self.assertEqual(result.parser_diagnostics, tuple(parsed.diagnostics))
        self.assertIsNone(result.features["hold"].data)
        parsed = parse_chart("(120){4}1h[4:1],E")
        parsed.last_event_end_s = float("nan")
        result = ChartAnalyzer().analyze(parsed)
        self.assertEqual(result.diagnostics[0].code, "INVALID_INPUT")
        self.assertIsNone(result.features["hold"].data)

    def test_config_order_failure_isolation_and_nonfinite_output(self):
        engine = ChartAnalyzer({
            "broken": FailingAnalyzer, "nan": NonFiniteAnalyzer, "HOLD": HoldFrequencyAnalyzer,
        })
        result = engine.analyze(parse_chart("(120){4}1h[4:1],E"))
        self.assertEqual(tuple(result.features), ("broken", "nan", "HOLD"))
        self.assertEqual(engine.feature_names, tuple(result.features))
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.features["HOLD"].data, 2)
        for name in ("broken", "nan"):
            self.assertIsNone(result.features[name].data)
            self.assertFalse(result.features[name].success)
        self.assertEqual([issue.feature for issue in result.diagnostics], ["broken", "nan"])

    def test_feature_mutation_does_not_leak_to_other_features_or_caller(self):
        parsed = parse_chart("(120){4}1h[4:1],E")
        original = deepcopy(parsed)
        engine = ChartAnalyzer({"mutates": MutatingAnalyzer, "hold": HoldFrequencyAnalyzer})
        for _ in range(2):
            result = engine.analyze(parsed)
            self.assertEqual(result.features["hold"].data, 2)
            self.assertEqual(parsed, original)

    def test_invalid_configuration_fails_early(self):
        for config in ({}, {"bad name": HoldFrequencyAnalyzer}, {"hold": "HoldFrequencyAnalyzer"}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                ChartAnalyzer(config)

    def test_core_import_does_not_load_filesystem_export_gui_or_scoring(self):
        process = subprocess.run([
            sys.executable, "-c",
            "import mairadar.analysis, sys; "
            "assert not {'mairadar.io', 'mairadar.batch', 'mairadar.exporters', "
            "'mairadar.scoring', 'tkinter'}.intersection(sys.modules)",
        ], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)


if __name__ == "__main__":
    unittest.main()
