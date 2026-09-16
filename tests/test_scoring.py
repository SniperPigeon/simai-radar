"""Dummy Pn anchor values and independent per-feature mapper configuration."""

from copy import deepcopy
import unittest
from unittest.mock import Mock

from mairadar.analysis import AnalysisResult, FeatureResult
from mairadar.scoring import DummyPnMapper, FeatureScoreTransformer, IdentityMapper
from mairadar.scoring.config import FEATURE_MAPPERS, TRANSFORMER


class ScaleMapper:
    def __init__(self, factor):
        self.factor = factor

    def map(self, data):
        return data * self.factor


class ScoringTests(unittest.TestCase):
    def test_default_mapping_keeps_jack_sweep_and_tricky_transparent(self):
        jack = FEATURE_MAPPERS["jack"]
        sweep = FEATURE_MAPPERS["sweep"]
        note = FEATURE_MAPPERS["note"]
        self.assertEqual((note.p50, note.p100), (3.540077197, 9.328672541))
        peak = FEATURE_MAPPERS["peak"]
        self.assertEqual((peak.p50, peak.p100), (10.0, 20.0))
        tricky = FEATURE_MAPPERS["slide_tricky"]
        sequence = FEATURE_MAPPERS["slide_sequence"]
        self.assertNotIn("slide_cumulate", FEATURE_MAPPERS)
        self.assertIsInstance(jack, IdentityMapper)
        self.assertIsInstance(sweep, IdentityMapper)
        self.assertIsInstance(tricky, IdentityMapper)
        self.assertEqual((sequence.p50, sequence.p100), (1.3, 2.9))
        transformer = TRANSFORMER()
        scores = transformer.transform(AnalysisResult({
            "jack": FeatureResult(8.5),
            "sweep": FeatureResult(4.25),
            "note": FeatureResult(note.p50),
            "peak": FeatureResult(peak.p50),
            "slide_tricky": FeatureResult(36.63092975357146),
            "slide_sequence": FeatureResult(sequence.p50),
        }))
        self.assertEqual(scores.features["jack"].value, 8.5)
        self.assertEqual(scores.features["sweep"].value, 4.25)
        self.assertEqual(scores.features["note"].value, 50)
        self.assertEqual(scores.features["peak"].value, 50)
        self.assertEqual(scores.features["slide_tricky"].value, 36.63092975357146)
        self.assertEqual(scores.features["slide_sequence"].value, 50)
        self.assertEqual(
            scores.mapping_version,
            "provisional-sweep-2s-top3-identity-20260916-v48",
        )

    def test_identity_mapper_validates_and_preserves_finite_values(self):
        mapper = IdentityMapper()
        for raw in (-3, 0, 1.25, 200):
            with self.subTest(raw=raw):
                self.assertEqual(mapper.map(raw), float(raw))
        for raw in (float("nan"), float("inf"), float("-inf"), None, True):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                mapper.map(raw)

    def test_two_segments_anchors_interiors_and_clamping(self):
        mapper = DummyPnMapper(p50=2, p100=6)
        for raw, expected in ((-1, 0), (0, 0), (1, 25), (2, 50), (3, 87.5),
                              (4, 125), (5, 162.5), (6, 200), (7, 200)):
            with self.subTest(raw=raw):
                self.assertEqual(mapper.map(raw), expected)

    def test_invalid_parameters_and_nonfinite_inputs_are_rejected(self):
        for p50, p100 in ((0, 1), (-1, 1), (2, 2), (3, 2), (True, 2),
                         (float("nan"), 2), (1, float("inf")), ("1", 2)):
            with self.subTest(p50=p50, p100=p100), self.assertRaises(ValueError):
                DummyPnMapper(p50, p100)
        mapper = DummyPnMapper(1, 2)
        for raw in (float("nan"), float("inf"), float("-inf"), None, True):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                mapper.map(raw)

    def test_features_can_choose_different_mapper_classes_and_parameters(self):
        source = AnalysisResult({name: FeatureResult(1.0) for name in ("first", "second", "third")})
        before = deepcopy(source)
        mapper = FeatureScoreTransformer({
            "first": DummyPnMapper(p50=1, p100=2),
            "second": DummyPnMapper(p50=2, p100=4),
            "third": ScaleMapper(factor=3),
        }, mapping_version="custom-test")
        scores = mapper.transform(source)
        self.assertEqual([item.value for item in scores.features.values()], [50, 25, 3])
        self.assertEqual(scores.mapping_version, "custom-test")
        self.assertEqual(source, before)

    def test_failed_features_are_not_mapped_and_mapping_errors_are_isolated(self):
        skipped, broken, nonfinite = Mock(), Mock(), Mock()
        broken.map.side_effect = ValueError("mapping failed")
        nonfinite.map.return_value = float("nan")
        source = AnalysisResult({
            "skipped": FeatureResult(None, success=False),
            **{name: FeatureResult(1.0) for name in ("broken", "missing", "nonfinite", "good")},
        })
        scores = FeatureScoreTransformer({
            "skipped": skipped, "broken": broken, "nonfinite": nonfinite,
            "good": DummyPnMapper(1, 2),
        }).transform(source)
        skipped.map.assert_not_called()
        self.assertEqual(scores.features["skipped"].status, "unavailable")
        for name in ("broken", "missing", "nonfinite"):
            self.assertEqual(scores.features[name].status, "error")
            self.assertIsNone(scores.features[name].value)
            self.assertEqual(scores.features[name].diagnostics[0].feature, name)
        self.assertEqual(scores.features["missing"].diagnostics[0].code, "MAPPER_MISSING")
        self.assertEqual(scores.features["good"].value, 50)


if __name__ == "__main__":
    unittest.main()
