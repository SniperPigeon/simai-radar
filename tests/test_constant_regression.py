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

from mairadar.constants import write_json, write_rows
from mairadar.regression import FEATURES, PolynomialModel
from mairadar.regression.__main__ import main
from mairadar.regression.training import fit_polynomial, powers_for_degree, train


def known_model():
    return {
        "schema_version": "mairadar-polynomial-1", "features": list(FEATURES), "input_kind": "raw",
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

    def test_rejects_invalid_schema_and_parameters(self):
        for key, value in (("schema_version", "unknown"), ("input_kind", "scores"),
                           ("features", list(reversed(FEATURES))), ("scale", [0] * 7),
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



@unittest.skipUnless(importlib.util.find_spec("numpy"), "optional NumPy training dependency unavailable")
class TrainingTests(unittest.TestCase):
    def samples(self):
        rng = random.Random(21)
        x = [[rng.uniform(-2, 2) for _ in FEATURES] for _ in range(160)]
        y = [12 + 0.4 * r[0] ** 2 - 0.3 * r[1] * r[2] + 0.2 * r[6] for r in x]
        return x, y

    def test_recovers_known_quadratic_on_unseen_samples(self):
        x, y = self.samples()
        model = fit_polynomial(x[:120], y[:120], degree=2, alpha=0)
        self.assertEqual(len(powers_for_degree(2)), 35)
        for raw, expected in zip(x[120:], y[120:]):
            self.assertAlmostEqual(model.predict(raw), expected, places=9)

    def test_constant_feature_and_singular_design_remain_finite(self):
        model = fit_polynomial([[3] * 7] * 20, [12.5] * 20, degree=3, alpha=1)
        self.assertAlmostEqual(model.predict([3] * 7), 12.5)
        self.assertEqual(model.scale, (1,) * 7)

    def test_group_holdout_and_export_roundtrip(self):
        x, y = self.samples()
        rows = [{"title": f"Song {i // 2}", "difficulty_index": 5 + i % 2,
                 "status": "ok", "official_constant": target, "match_status": "matched",
                 **{f"{f}_raw": v for f, v in zip(FEATURES, raw)}}
                for i, (raw, target) in enumerate(zip(x, y))]
        rows.append({**rows[0], "title": "Unknown", "match_status": "title_not_found"})
        model, evaluation, report, predictions, vectors = train(rows, degrees=[1, 2], alphas=[0], folds=3)
        self.assertEqual(report["selected"]["degree"], 2)
        self.assertEqual(report["training_rows"], 160)
        self.assertEqual(report["excluded"], {"no_matched_constant": 1})
        self.assertLess(report["holdout"]["rmse"], 1e-9)
        self.assertTrue(set(report["development_titles"]).isdisjoint(report["holdout_titles"]))
        for title in report["holdout_titles"]:
            selected = [p for p in predictions if p["title"] == title]
            self.assertEqual(len(selected), 2)
            self.assertTrue(all(p["evaluation_split"] == "holdout" for p in selected))
        restored = PolynomialModel(json.loads(json.dumps(model.to_dict())))
        for vector in vectors:
            self.assertAlmostEqual(restored.predict(vector["raw"]), vector["expected"], places=9)
        self.assertEqual(predictions[-1]["prediction_status"], "ok")
        for row in predictions:
            if row["evaluation_split"] == "holdout":
                raw = [row[f"{f}_raw"] for f in FEATURES]
                self.assertAlmostEqual(evaluation.predict(raw), row["holdout_prediction"], places=9)


if __name__ == "__main__":
    unittest.main()
