"""Raw stats and model predictions remain independent from scoring and radar axes."""

from copy import deepcopy
import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mairadar.analysis import AnalysisResult, ChartAnalyzer, FeatureResult, flatten_features
from mairadar.analysis.statistics import summarize
from mairadar.constants import read_rows, write_json
from mairadar.exporters import VisualizerExporter
from mairadar.exporters import visualizer
from mairadar.exporters.constants import ConstantAnnotations
from mairadar.exporters.visualizer import select_dimensions
from mairadar.parser import parse_chart
from mairadar.pipeline import run_pipeline
from mairadar.regression import PolynomialModel
from mairadar.regression.derived import with_prediction
from mairadar.scoring import FeatureScoreTransformer


class A:
    def analyze(self, context):
        return FeatureResult(100, stats={"mean": 3, "missing": None})


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
        "schema_version": "mairadar-polynomial-2", "input_kind": "raw", "features": ["a_mean", "b"],
        "center": [0, 0], "scale": [1, 1], "intercept": 12,
        "terms": [{"powers": [1, 0], "coefficient": 2}, {"powers": [0, 1], "coefficient": -1}],
    })


class MultiFeatureTests(unittest.TestCase):
    def test_stats_summarize_analyzer_samples(self):
        result = ChartAnalyzer().analyze(parse_chart("(120){4}1,2,3,4,E"))
        note = result.features["note"]
        self.assertAlmostEqual(note.stats["density_mean"], 4 / 3)
        self.assertAlmostEqual(note.stats["density_max"], 2)
        stats = summarize([1, 3], "x")
        self.assertEqual(stats["x_mean"], 2)
        self.assertEqual(stats["x_median"], 2)
        self.assertAlmostEqual(stats["x_rms"], 5 ** 0.5)
        self.assertAlmostEqual(stats["x_p95"], 2.9)
        self.assertEqual(summarize([], "x")["x_max"], 0)

    def test_raw_prediction_precedes_mapping_and_does_not_mutate_input(self):
        original = ChartAnalyzer({"a": A, "b": B}).analyze(parse_chart("(120)1,E"))
        before = deepcopy(original)
        derived = with_prediction(original, model())
        self.assertEqual(original, before)
        self.assertEqual(derived.features["fitted_constant"].data, 17)
        mapped = FeatureScoreTransformer({"a_mean": Scale(100), "fitted_constant": Scale(2)}).transform(derived)
        self.assertEqual(mapped.features["a_mean"].value, 300)
        self.assertEqual(mapped.features["fitted_constant"].value, 34)
        self.assertNotIn("a", mapped.features)
        self.assertEqual(derived.features["fitted_constant"].data, 17)
        self.assertFalse(flatten_features(original)["a_missing"].success)

    def test_missing_model_input_is_diagnosed_but_base_values_remain(self):
        result = with_prediction(AnalysisResult({"a": FeatureResult(100)}), model())
        self.assertFalse(result.features["fitted_constant"].success)
        self.assertEqual(result.features["a"].data, 100)
        self.assertEqual(result.diagnostics[-1].code, "PREDICTION_FAILED")
        invalid = model().to_dict()
        invalid["features"][0] = "fitted_constant"
        with self.assertRaisesRegex(ValueError, "own fitted_constant"):
            with_prediction(result, PolynomialModel(invalid))

    def test_pipeline_exports_full_raw_values_and_independent_mapped_axes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "chart.simai"
            source.write_text("(120){4}1,E")
            mapper = FeatureScoreTransformer({"a": Scale(10), "a_mean": Scale(100),
                                               "b": Scale(10), "fitted_constant": Scale(2)})
            kwargs = {"analyzer": ChartAnalyzer({"a": A, "b": B}), "transformer": mapper,
                      "constant_model": model()}
            _, csv_report = run_pipeline("full", source, output=root / "csv", **kwargs)
            with csv_report.csv_path.open(encoding="utf-8-sig") as stream:
                [row] = list(csv.DictReader(stream))
            self.assertEqual(float(row["fitted_constant_raw"]), 17)
            self.assertEqual(float(row["fitted_constant_score"]), 34)
            self.assertEqual(float(row["a_mean_raw"]), 3)
            self.assertNotIn("a_missing_score", row)
            with patch.object(visualizer, "RADAR_FEATURES", ("a_mean", "fitted_constant", "b")):
                _, report = run_pipeline("full", source, output=root / "site", **kwargs,
                                         exporter=VisualizerExporter())
            payload = json.loads(report.data_path.read_text())
            self.assertEqual([axis["key"] for axis in payload["dimensions"]], ["a_mean", "fitted_constant", "b"])
            chart = payload["songs"][0]["charts"][0]
            self.assertEqual(chart["scores"], {"a_mean": 300, "fitted_constant": 34, "b": 10})
            self.assertEqual(chart["fittedConstant"], 17)
            self.assertEqual(chart["rawFeatures"]["a"], 100)
            script = Path(__file__).resolve().parents[1] / "scripts/build_pages.py"
            spec = importlib.util.spec_from_file_location("multifeature_pages", script)
            pages = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(pages)
            target = root / "repackaged"
            target.mkdir()
            with patch.object(visualizer, "RADAR_FEATURES", ("b", "a")):
                pages.prepare_site(root / "site", target, no_covers=True)
            repackaged = json.loads((target / "data/songs.json").read_text())
            self.assertEqual([axis["key"] for axis in repackaged["dimensions"]], ["b", "a"])
            updated = repackaged["songs"][0]["charts"][0]
            self.assertEqual(updated["scores"], {"b": 10, "a": 1000})
            self.assertEqual(updated["rawFeatures"], chart["rawFeatures"])
            self.assertEqual(updated["mappedFeatures"], chart["mappedFeatures"])
            select_dimensions(payload, ["b"])
            self.assertEqual(chart["rawFeatures"]["a_mean"], 3)
            write_json(root / "selected.json", payload)
            [reloaded] = read_rows(root / "selected.json")
            self.assertEqual(reloaded["a_mean_raw"], 3)
            self.assertEqual(model().predict(chart["rawFeatures"]), 17)
            with self.assertRaisesRegex(ValueError, "score mapping"):
                select_dimensions(payload, ["a_missing"])

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
                "scores": {"b": 999}, "rawFeatures": {"a_mean": 3, "b": 1}, "mappedFeatures": {"b": 999},
            }]}]}
            adapter = ConstantAnnotations(model=root / "model.json", mapping_profile=root / "profile.json")
            self.assertEqual(adapter.payload(payload), 0)
            select_dimensions(payload, ["fitted_constant"])
            chart = payload["songs"][0]["charts"][0]
            self.assertEqual(chart["scores"]["fitted_constant"], 50)
            self.assertEqual(chart["rawScores"]["fitted_constant"], 17)
            self.assertEqual(chart["fittedConstant"], 17)

    @unittest.skipUnless(importlib.util.find_spec("sklearn"), "optional training dependency")
    def test_training_selects_stats_columns_and_exports_dynamic_input_order(self):
        from mairadar.regression import training
        rows = [{"title": "X", "level": "13", "status": "ok", "match_status": "matched",
                 "a_mean_raw": i / 100, "b_raw": (i % 3) / 10,
                 "official_constant": 12 + 2 * i / 100 - (i % 3) / 10} for i in range(60)]
        with patch.object(training, "TRAINING_FEATURES", ("a_mean", "b")):
            fitted, _, report, _, _ = training.train(rows, degrees=[1], alphas=[0], folds=3)
        # Restore the normal training selection before loading and using the model.
        fitted = PolynomialModel(json.loads(json.dumps(fitted.to_dict())))
        self.assertAlmostEqual(fitted.predict({"b": 0.2, "a_mean": 0.8, "irrelevant": 1000}), 13.4, places=9)
        self.assertLess(report["holdout"]["rmse"], 1e-9)
        with self.assertRaisesRegex(ValueError, "own training input"):
            training.train(rows, features=["fitted_constant"], degrees=[1], alphas=[0])


if __name__ == "__main__":
    unittest.main()
