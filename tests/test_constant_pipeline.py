"""Prediction is a scalar feature; score mapping and radar projection follow it."""

from copy import deepcopy
import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mairadar.analysis import AnalysisResult, ChartAnalyzer, FeatureResult
from mairadar.constants import read_rows, write_json
from mairadar.exporters import VisualizerExporter, visualizer
from mairadar.exporters.constants import ConstantAnnotations
from mairadar.exporters.visualizer import select_dimensions
from mairadar.parser import parse_chart
from mairadar.pipeline import run_pipeline
from mairadar.regression import PolynomialModel
from mairadar.regression.derived import with_prediction
from mairadar.scoring import FeatureScoreTransformer


class A:
    def analyze(self, context):
        return FeatureResult(3)


class B:
    def analyze(self, context):
        return FeatureResult(1)


class Scale:
    def __init__(self, scale):
        self.scale = scale

    def map(self, value):
        return value * self.scale


def model():
    return PolynomialModel({
        "schema_version": "mairadar-polynomial-2", "input_kind": "raw", "features": ["a", "b"],
        "center": [0, 0], "scale": [1, 1], "intercept": 12,
        "terms": [{"powers": [1, 0], "coefficient": 2}, {"powers": [0, 1], "coefficient": -1}],
    })


class ConstantPipelineTests(unittest.TestCase):
    def test_raw_prediction_precedes_mapping_and_does_not_mutate_input(self):
        original = ChartAnalyzer({"a": A, "b": B}).analyze(parse_chart("(120)1,E"))
        before = deepcopy(original)
        derived = with_prediction(original, model())
        self.assertEqual(original, before)
        self.assertEqual(derived.features["fitted_constant"].data, 17)
        mapped = FeatureScoreTransformer({"a": Scale(100), "fitted_constant": Scale(2)}).transform(derived)
        self.assertEqual(mapped.features["a"].value, 300)
        self.assertEqual(mapped.features["fitted_constant"].value, 34)
        self.assertNotIn("b", mapped.features)
        self.assertEqual(derived.features["fitted_constant"].data, 17)

    def test_missing_model_input_is_diagnosed_but_base_values_remain(self):
        result = with_prediction(AnalysisResult({"a": FeatureResult(100)}), model())
        self.assertFalse(result.features["fitted_constant"].success)
        self.assertEqual(result.features["a"].data, 100)
        self.assertEqual(result.diagnostics[-1].code, "PREDICTION_FAILED")
        invalid = model().to_dict()
        invalid["features"][0] = "fitted_constant"
        with self.assertRaisesRegex(ValueError, "own fitted_constant"):
            with_prediction(result, PolynomialModel(invalid))

    def test_pipeline_exports_prediction_and_retains_raw_values_when_radar_axes_change(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "chart.simai"
            source.write_text("(120){4}1,E")
            mapper = FeatureScoreTransformer({"a": Scale(100), "b": Scale(10), "fitted_constant": Scale(2)})
            kwargs = {"analyzer": ChartAnalyzer({"a": A, "b": B}), "transformer": mapper,
                      "constant_model": model()}
            _, csv_report = run_pipeline("full", source, output=root / "csv", **kwargs)
            with csv_report.csv_path.open(encoding="utf-8-sig") as stream:
                [row] = list(csv.DictReader(stream))
            self.assertEqual(float(row["fitted_constant_raw"]), 17)
            self.assertEqual(float(row["fitted_constant_score"]), 34)
            self.assertEqual(float(row["a_raw"]), 3)
            with patch.object(visualizer, "RADAR_FEATURES", ("fitted_constant", "b")):
                _, report = run_pipeline("full", source, output=root / "site", **kwargs,
                                         exporter=VisualizerExporter())
            payload = json.loads(report.data_path.read_text())
            self.assertEqual([axis["key"] for axis in payload["dimensions"]], ["fitted_constant", "b"])
            chart = payload["songs"][0]["charts"][0]
            self.assertEqual(chart["scores"], {"fitted_constant": 34, "b": 10})
            self.assertEqual(chart["fittedConstant"], 17)
            self.assertEqual(chart["rawFeatures"]["a"], 3)
            script = Path(__file__).resolve().parents[1] / "scripts/build_pages.py"
            spec = importlib.util.spec_from_file_location("constant_pages", script)
            pages = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(pages)
            target = root / "repackaged"
            target.mkdir()
            with patch.object(visualizer, "RADAR_FEATURES", ("a",)):
                pages.prepare_site(root / "site", target, no_covers=True)
            repackaged = json.loads((target / "data/songs.json").read_text())
            self.assertEqual([axis["key"] for axis in repackaged["dimensions"]], ["a"])
            updated = repackaged["songs"][0]["charts"][0]
            self.assertEqual(updated["scores"], {"a": 300})
            self.assertEqual(updated["rawFeatures"], chart["rawFeatures"])
            self.assertEqual(updated["mappedFeatures"], chart["mappedFeatures"])
            [reloaded] = read_rows(target)
            self.assertEqual(reloaded["b_raw"], 1)
            self.assertEqual(model().predict(updated["rawFeatures"]), 17)
            with self.assertRaisesRegex(ValueError, "score mapping"):
                select_dimensions(payload, ["missing"])

    def test_existing_site_prediction_is_raw_then_profile_mapping_then_axis_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_json(root / "model.json", model().to_dict())
            write_json(root / "profile.json", {
                "schemaVersion": "mairadar-mapping-profile-1", "mappingVersion": "test",
                "scoreAnchors": [50, 100, 150, 200], "maximumScore": 220,
                "dimensions": {"fitted_constant": {"rawAnchors": [17, 18, 19, 20], "t4Max": 21}},
            })
            payload = {"dimensions": [{"key": "b"}], "songs": [{"title": "X", "charts": [{
                "difficulty": 5, "kind": "ST", "status": "ok", "rawScores": {"b": 1},
                "scores": {"b": 999}, "rawFeatures": {"a": 3, "b": 1}, "mappedFeatures": {"b": 999},
            }]}]}
            adapter = ConstantAnnotations(model=root / "model.json", mapping_profile=root / "profile.json")
            self.assertEqual(adapter.payload(payload), 0)
            select_dimensions(payload, ["fitted_constant"])
            chart = payload["songs"][0]["charts"][0]
            self.assertEqual(chart["scores"]["fitted_constant"], 50)
            self.assertEqual(chart["rawScores"]["fitted_constant"], 17)
            self.assertEqual(chart["fittedConstant"], 17)


if __name__ == "__main__":
    unittest.main()
