"""Polynomial math, grouped evaluation, portable inference and UI adapters."""

import contextlib
import importlib.util
import io
import json
import math
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
import warnings
from unittest.mock import patch

from mairadar.constants import write_json, write_rows
from mairadar.cli import main as analyze
from mairadar.exporters.constants import ConstantAnnotations
from mairadar.regression import PolynomialModel
from mairadar.regression.__main__ import main

FEATURES = ("note", "peak", "sweep", "slide_tricky", "slide_sequence", "jack", "slide_cumulate")
SKLEARN_AVAILABLE = importlib.util.find_spec("sklearn") is not None
if SKLEARN_AVAILABLE:
    import numpy as np
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import PolynomialFeatures, StandardScaler
    from mairadar.regression.training import export_model, train


def known_model():
    return {
        "schema_version": "mairadar-polynomial-2", "features": list(FEATURES), "input_kind": "raw",
        "center": [1] * 7, "scale": [2] * 7, "intercept": 10,
        "terms": [
            {"powers": [2, 0, 0, 0, 0, 0, 0], "coefficient": 2},
            {"powers": [1, 1, 0, 0, 0, 0, 0], "coefficient": -3},
        ],
    }


class RuntimeTests(unittest.TestCase):
    def test_hand_computed_interactions_and_feature_order(self):
        model = PolynomialModel(known_model())
        self.assertEqual(model.predict([5, 3, 1, 1, 1, 1, 1]), 12)
        self.assertEqual(model.predict(dict(zip(FEATURES, [5, 3, 1, 1, 1, 1, 1]))), 12)
        for bad in ([1] * 6, [1] * 6 + [None], [1] * 6 + [math.nan], [True] * 7, {}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                model.predict(bad)

        higher_degree = known_model()
        higher_degree["degree"] = 5
        higher_degree["terms"] = [{"powers": [5, 0, 0, 0, 0, 0, 0], "coefficient": 2}]
        self.assertEqual(PolynomialModel(higher_degree).predict([5, 1, 1, 1, 1, 1, 1]), 74)

    def test_rejects_invalid_schema_and_parameters(self):
        for key, value in (("schema_version", "unknown"), ("input_kind", "scores"),
                           ("features", ["duplicate"] * 7), ("scale", [0] * 7),
                           ("intercept", math.inf), ("center", [1] * 6),
                           ("terms", [{"powers": [1.0] * 7, "coefficient": 1}])):
            data = known_model()
            data[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                PolynomialModel(data)

    def test_standalone_runtime_runs_with_site_packages_disabled(self):
        runtime = Path(__file__).resolve().parents[1] / "src/mairadar/regression/runtime.py"
        with tempfile.TemporaryDirectory() as temp:
            model_path = Path(temp) / "model.json"
            write_json(model_path, known_model())
            code = (
                "import importlib.util,sys; "
                "s=importlib.util.spec_from_file_location('portable',sys.argv[1]); "
                "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
                "print(m.PolynomialModel.load(sys.argv[2]).predict([5,3,1,1,1,1,1]))"
            )
            result = subprocess.run([sys.executable, "-S", "-c", code, str(runtime), str(model_path)],
                                    capture_output=True, text=True, check=True)
            self.assertEqual(float(result.stdout), 12)

    def test_visualizer_uses_raw_and_leaves_missing_values_null(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_json(root / "model.json", known_model())
            write_rows(root / "constants.csv", [
                {"title": "X", "difficulty_index": 5, "chart_type": "sd",
                 "official_constant": 12.4, "match_status": "matched"},
            ])
            annotation = ConstantAnnotations(root / "constants.csv", root / "model.json")
            raw = dict(zip(FEATURES, [5, 3, 1, 1, 1, 1, 1]))
            result = annotation.chart("X", 5, "ST", raw)
            self.assertEqual((result["officialConstant"], result["fittedConstant"]), (12.4, 12))
            missing = annotation.chart("Y", 5, "DX", {})
            self.assertIsNone(missing["officialConstant"])
            self.assertIsNone(missing["fittedConstant"])

    def test_predict_cli_partial_failure_is_nonzero(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_json(root / "model.json", known_model())
            rows = [{"title": "X", "difficulty_index": 5,
                     **{f"{f}_raw": 1 for f in FEATURES}}, {"title": "Y", "difficulty_index": 5}]
            write_rows(root / "charts.csv", rows)
            with contextlib.redirect_stdout(io.StringIO()):
                result = main(["predict", "--input", str(root / "charts.csv"),
                               "--model", str(root / "model.json"), "--output", str(root / "predictions.csv")])
            self.assertEqual(result, 1)
            self.assertTrue((root / "predictions.csv").is_file())

    def test_pipeline_exports_both_constants_and_reuses_site_without_analysis(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "maidata.txt"
            source.write_text("&title=Synthetic\n&cabinet=SD\n&lv_5=12\n&inote_5=(120){4}1,2,3,4,E\n")
            write_json(root / "model.json", known_model())
            write_rows(root / "constants.csv", [
                {"title": "Synthetic", "difficulty_index": 5, "chart_type": "sd",
                 "official_constant": 12.4, "match_status": "matched"},
            ])
            with contextlib.redirect_stdout(io.StringIO()):
                result = analyze([
                    "--mode", "full", "--input", str(source), "--output", str(root / "site"),
                    "--format", "visualizer", "--constants-table", str(root / "constants.csv"),
                    "--constant-model", str(root / "model.json"),
                ])
            self.assertEqual(result, 0)
            payload = json.loads((root / "site/data/songs.json").read_text())
            chart = payload["songs"][0]["charts"][0]
            self.assertEqual(chart["officialConstant"], 12.4)
            self.assertEqual(chart["fittedConstant"], PolynomialModel(known_model()).predict(chart["rawScores"]))
            self.assertEqual(payload["stats"]["constantPredictionFailedCount"], 0)
            script = Path(__file__).resolve().parents[1] / "scripts/build_pages.py"
            spec = importlib.util.spec_from_file_location("test_build_pages", script)
            pages = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(pages)
            chart["exportIssues"] = ["Conflicting cover filename: Synthetic.png"]
            with self.assertRaisesRegex(ValueError, "incomplete chart"):
                pages.validate_payload(payload)
            self.assertEqual(len(pages.validate_payload(payload, include_covers=False)), 1)
            chart["status"] = "partial"
            with self.assertRaisesRegex(ValueError, "incomplete chart"):
                pages.validate_payload(payload, include_covers=False)
            target = root / "updated"
            target.mkdir()
            pages.prepare_site(root / "site", target, no_covers=True,
                               constants_table=root / "constants.csv", constant_model=root / "model.json")
            self.assertTrue((target / "data/songs.json").is_file())


@unittest.skipUnless(SKLEARN_AVAILABLE, "optional sklearn training dependency unavailable")
class TrainingTests(unittest.TestCase):
    def rows(self):
        rng = random.Random(21)
        rows = []
        for i in range(160):
            raw = [rng.uniform(-2, 2) for _ in FEATURES]
            target = 12 + 0.4 * raw[0] ** 2 - 0.3 * raw[1] * raw[2] + 0.2 * raw[-1]
            rows.append({"title": "Same Song", "difficulty_index": 5, "level": "13+" if i < 80 else "14",
                         "status": "ok", "match_status": "matched", "official_constant": target,
                         **{f"{name}_raw": value for name, value in zip(FEATURES, raw)}})
        return rows

    def test_exported_formula_matches_sklearn_on_unseen_inputs(self):
        rng = np.random.default_rng(21)
        x = rng.normal(size=(180, len(FEATURES)))
        x[:, -1] = 3  # A constant column must export a usable scale.
        y = 12 + x[:, 0] ** 2 - x[:, 1] * x[:, 2]
        unseen = rng.normal(size=(25, len(FEATURES)))
        pipeline = make_pipeline(StandardScaler(), PolynomialFeatures(2, include_bias=False),
                                 Ridge(alpha=1, solver="svd")).fit(x, y)
        model = PolynomialModel(json.loads(json.dumps(export_model(pipeline, FEATURES).to_dict())))
        np.testing.assert_allclose([model.predict(row) for row in unseen], pipeline.predict(unseen),
                                   rtol=1e-10, atol=1e-10)

    def test_training_splits_rows_by_level_and_retains_excluded_predictions(self):
        rows = self.rows()
        rows.append({**rows[0], "match_status": "constant_estimated", "official_constant": None})
        model, evaluation, report, predictions, vectors = train(rows, alphas=[0], folds=3)
        self.assertLess(report["holdout"]["rmse"], 1e-8)
        development = set(report["development_row_numbers"])
        holdout = set(report["holdout_row_numbers"])
        self.assertFalse(development & holdout)
        self.assertEqual(development | holdout, set(range(1, len(rows))))
        validation = report["cv_validation_row_numbers"]
        self.assertEqual(set().union(*map(set, validation)), development)
        self.assertEqual(sum(map(len, validation)), len(development))
        for level in {row["level"] for row in rows[:-1]}:
            total = sum(row["level"] == level for row in rows[:-1])
            selected = sum(rows[i - 1]["level"] == level for i in holdout)
            self.assertLessEqual(abs(selected - total * 0.2), 1)
        self.assertEqual(predictions[-1]["evaluation_split"], "excluded")
        self.assertIsNone(predictions[-1]["holdout_prediction"])
        self.assertTrue(math.isfinite(predictions[-1]["fitted_constant"]))
        for number in holdout:
            row = predictions[number - 1]
            raw = [row[f"{name}_raw"] for name in FEATURES]
            self.assertAlmostEqual(evaluation.predict(raw), row["holdout_prediction"], places=9)
        for vector in vectors:
            self.assertAlmostEqual(model.predict(vector["raw"]), vector["expected"], places=9)

    def test_holdout_values_do_not_influence_preprocessing_or_model_selection(self):
        rows = self.rows()
        _, before, report, _, _ = train(rows, alphas=[1], folds=3)
        for number in report["holdout_row_numbers"]:
            for name in FEATURES:
                rows[number - 1][f"{name}_raw"] += 100
        _, after, repeated, _, _ = train(rows, alphas=[1], folds=3)
        self.assertEqual(report["holdout_row_numbers"], repeated["holdout_row_numbers"])
        probe = [0.5] * len(FEATURES)
        self.assertAlmostEqual(before.predict(probe), after.predict(probe), places=12)
        self.assertEqual(before.to_dict(), after.to_dict())

    def test_singleton_level_can_still_be_used_for_training(self):
        rows = self.rows()
        rows.append({**rows[0], "level": "15"})
        with warnings.catch_warnings(record=True):
            _, _, report, _, _ = train(rows, alphas=[1], folds=3)
        self.assertIn(len(rows), report["development_row_numbers"])
        self.assertNotIn(len(rows), report["holdout_row_numbers"])

    def test_fit_cli_exports_a_model_usable_by_the_prediction_cli(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_rows(root / "constants.csv", self.rows())
            with contextlib.redirect_stdout(io.StringIO()), patch("mairadar.regression.training.TRAINING_FEATURES", FEATURES):
                self.assertEqual(main(["fit", "--input", str(root / "constants.csv"),
                                       "--output", str(root / "fit"), "--folds", "3"]), 0)
                self.assertEqual(main(["predict", "--input", str(root / "constants.csv"),
                                       "--model", str(root / "fit/model.json"),
                                       "--output", str(root / "predictions.csv")]), 0)
            expected = json.loads((root / "fit/test_vectors.json").read_text())["vectors"]
            restored = PolynomialModel.load(root / "fit/model.json")
            self.assertEqual(restored.features, FEATURES)
            self.assertEqual(restored.to_dict()["degree"], 2)
            for vector in expected:
                self.assertAlmostEqual(restored.predict(vector["raw"]), vector["expected"], places=9)


if __name__ == "__main__":
    unittest.main()
