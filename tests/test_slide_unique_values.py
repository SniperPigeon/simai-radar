"""Stable unique-value selection for the experimental Slide tricky tail."""

import unittest

from mairadar.analysis.features.slide import _top_unique_tricky_load


class SlideUniqueValueTests(unittest.TestCase):
    def test_repeated_values_occupy_one_of_five_slots(self):
        self.assertEqual(
            _top_unique_tricky_load([10, 10, 10, 8, 8, 6, 4, 2, 1]),
            30.0,
        )

    def test_missing_slots_are_implicitly_zero(self):
        self.assertEqual(_top_unique_tricky_load([7, 7]), 7.0)
        self.assertEqual(_top_unique_tricky_load([]), 0.0)

    def test_six_decimal_bucket_ignores_float_noise_and_keeps_maximum(self):
        values = [3.0000001, 3.0000004, 2.0, 1.0]
        self.assertAlmostEqual(_top_unique_tricky_load(values), 6.0000004)


if __name__ == "__main__":
    unittest.main()
