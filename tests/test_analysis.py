"""MVP metrics and independent in-memory analyzer dispatch."""

from copy import deepcopy
import subprocess
import sys
import unittest

from mairadar.analysis import ChartAnalyzer, FeatureResult
from mairadar.analysis.features import JackSequenceAnalyzer
from mairadar.parser import parse_chart


class TimingCountAnalyzer:
    def analyze(self, context):
        return FeatureResult(sum(event.kind == "timing" for event in context.events))


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

    def test_tempo_change_and_redundant_declaration(self):
        first = ChartAnalyzer().analyze(parse_chart("(180){16}1,1,(180)2,2,E"))
        repeated = ChartAnalyzer().analyze(parse_chart("(180)(180){16}1,1,(180)(180)2,2,E"))
        self.assertEqual(first.features, repeated.features)

    def test_no_jack_is_zero_but_zero_time_is_unavailable(self):
        for text in ("(120){4}1,2,E", "(120){4},,E"):
            with self.subTest(text=text):
                result = ChartAnalyzer().analyze(parse_chart(text))
                self.assertEqual(result.features["jack"].data, 0)
                self.assertEqual(result.status, "ok")
        result = ChartAnalyzer().analyze(parse_chart("E"))
        self.assertIsNone(result.features["jack"].data)
        self.assertFalse(result.features["jack"].success)
        self.assertEqual(result.status, "error")

    def test_incomplete_and_invalid_inputs_do_not_produce_plausible_values(self):
        parsed = parse_chart("(120){8}1,1,invalid,E")
        result = ChartAnalyzer().analyze(parsed)
        self.assertEqual(result.diagnostics[0].code, "PARSE_INCOMPLETE")
        self.assertEqual(result.parser_diagnostics, tuple(parsed.diagnostics))
        self.assertIsNone(result.features["jack"].data)
        parsed = parse_chart("(120){8}1,1,E")
        parsed.last_event_end_s = float("nan")
        result = ChartAnalyzer().analyze(parsed)
        self.assertEqual(result.diagnostics[0].code, "INVALID_INPUT")
        self.assertIsNone(result.features["jack"].data)

    def test_config_order_failure_isolation_and_nonfinite_output(self):
        engine = ChartAnalyzer({
            "broken": FailingAnalyzer, "nan": NonFiniteAnalyzer, "timings": TimingCountAnalyzer,
        })
        result = engine.analyze(parse_chart("(180){16}1,1,E"))
        self.assertEqual(tuple(result.features), ("broken", "nan", "timings"))
        self.assertEqual(engine.feature_names, tuple(result.features))
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.features["timings"].data, 1)
        for name in ("broken", "nan"):
            self.assertIsNone(result.features[name].data)
            self.assertFalse(result.features[name].success)
        self.assertEqual([issue.feature for issue in result.diagnostics], ["broken", "nan"])

    def test_feature_mutation_does_not_leak_to_other_features_or_caller(self):
        parsed = parse_chart("(180){16}1,1,E")
        original = deepcopy(parsed)
        engine = ChartAnalyzer({"mutates": MutatingAnalyzer, "timings": TimingCountAnalyzer})
        for _ in range(2):
            result = engine.analyze(parsed)
            self.assertEqual(result.features["timings"].data, 1)
            self.assertEqual(parsed, original)

    def test_invalid_configuration_fails_early(self):
        for config in ({}, {"bad name": JackSequenceAnalyzer}, {"jack": "JackSequenceAnalyzer"}):
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
