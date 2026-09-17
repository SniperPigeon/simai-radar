"""Frozen mapping profiles and open-set extrapolation."""

import json
import math
from pathlib import Path
import tempfile
import unittest

from mairadar.analysis import AnalysisResult, FeatureResult
from mairadar.scoring import OpenSetPiecewiseMapper, load_mapping_profile


class MappingProfileTests(unittest.TestCase):

    def test_piecewise_anchors_plateau_and_asymptotic_tail(self):
        mapper = OpenSetPiecewiseMapper((1, 2, 3, 4), t4_max=10)
        for raw, expected in (
            (-1, 0), (0, 0), (0.5, 25), (1, 50), (1.5, 75),
            (2, 100), (2.5, 125), (3, 150), (3.5, 175),
            (4, 200), (7, 200), (10, 200),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(mapper.map(raw), expected)

        epsilon = 1e-7
        initial_tail_slope = (mapper.map(10 + epsilon) - mapper.map(10)) / epsilon
        self.assertAlmostEqual(initial_tail_slope, 50, places=4)
        self.assertGreater(mapper.map(10.1), 200)
        self.assertLess(mapper.map(10.1), 220)
        self.assertAlmostEqual(mapper.map(1_000_000), 220)

    def test_profile_loads_all_dimensions_and_preserves_version(self):
        payload = {
            "schemaVersion": "mairadar-mapping-profile-1",
            "mappingVersion": "test-profile-v1",
            "scoreAnchors": [50, 100, 150, 200],
            "maximumScore": 220,
            "dimensions": {
                "note": {"rawAnchors": [1, 2, 3, 4], "t4Max": 8},
                "peak": {"rawAnchors": [2, 4, 6, 8], "t4Max": 9},
            },
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "mapping_profile.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            transformer = load_mapping_profile(path)

        scores = transformer.transform(AnalysisResult({
            "note": FeatureResult(9),
            "peak": FeatureResult(8),
        }))
        self.assertEqual(scores.mapping_version, "test-profile-v1")
        self.assertGreater(scores.features["note"].value, 200)
        self.assertLess(scores.features["note"].value, 220)
        self.assertEqual(scores.features["peak"].value, 200)

    def test_invalid_profiles_and_mapping_inputs_are_rejected(self):
        for anchors, t4_max in (
            ((0, 2, 3, 4), 5),
            ((1, 2, 2, 4), 5),
            ((1, 2, 3, 4), 3),
            ((1, 2, 3, math.inf), 5),
        ):
            with self.subTest(anchors=anchors, t4_max=t4_max), self.assertRaises(ValueError):
                OpenSetPiecewiseMapper(anchors, t4_max)
        mapper = OpenSetPiecewiseMapper((1, 2, 3, 4), 5)
        for value in (None, True, math.nan, math.inf):
            with self.subTest(value=value), self.assertRaises(ValueError):
                mapper.map(value)


if __name__ == "__main__":
    unittest.main()
