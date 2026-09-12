import unittest

from mairadar.constant_reference import (
    MAJDATAPLAY_PIN,
    MAJDATAPLAY_STANDARD_SLIDE_BAR_COUNTS,
    MAJSIMAI_PIN,
)
from mairadar.model import MAJDATAPLAY_PIN as MODEL_MAJDATAPLAY_PIN
from mairadar.model import MAJSIMAI_PIN as MODEL_MAJSIMAI_PIN


class ConstantReferenceTests(unittest.TestCase):
    def test_model_keeps_reference_pin_compatibility(self):
        self.assertEqual(MODEL_MAJDATAPLAY_PIN, MAJDATAPLAY_PIN)
        self.assertEqual(MODEL_MAJSIMAI_PIN, MAJSIMAI_PIN)

    def test_standard_slide_bar_count_anchors(self):
        counts = MAJDATAPLAY_STANDARD_SLIDE_BAR_COUNTS
        self.assertEqual(counts["line3"], 14)
        self.assertEqual(counts["line5"], 20)
        self.assertEqual(counts["circle1"], 64)
        self.assertEqual(counts["circle2"], 8)
        self.assertEqual(counts["pq6"], 43)
        self.assertEqual(counts["ppqq4"], 50)
        self.assertEqual(counts["s"], 31)
        self.assertEqual(counts["wifi"], 12)
        self.assertEqual(counts["L3"], 35)

    def test_v1_uses_its_dedicated_prefab_length(self):
        self.assertEqual(MAJDATAPLAY_STANDARD_SLIDE_BAR_COUNTS["v1"], 21)

    def test_reference_mapping_is_read_only(self):
        with self.assertRaises(TypeError):
            MAJDATAPLAY_STANDARD_SLIDE_BAR_COUNTS["line5"] = 999  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
