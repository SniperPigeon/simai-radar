"""Two-hand displacement DP and idle reposition accounting."""

import unittest

from mairadar.analysis.features.hand_motion import two_hand_motion


class HandMotionTests(unittest.TestCase):
    def test_adjacent_single_sweep_stays_on_one_hand(self):
        result = two_hand_motion((0.0, 0.1, 0.2), ((1,), (2,), (3,)))
        self.assertEqual(result.total_distance, 2)
        self.assertEqual(result.active_distance, 2)
        self.assertEqual(result.idle_reposition_distance, 0)
        self.assertEqual(result.free_hand_takeovers, 0)
        self.assertEqual(result.fast_jump_violations, 0)

    def test_free_hand_takes_a_fast_non_adjacent_target_without_teleporting(self):
        result = two_hand_motion((0.0, 0.1, 0.2), ((1,), (2,), (6,)))
        self.assertEqual(result.total_distance, 1)
        self.assertEqual(result.active_distance, 1)
        self.assertEqual(result.idle_reposition_distance, 0)
        self.assertEqual(result.free_hand_takeovers, 1)
        self.assertEqual(result.fast_jump_violations, 0)

    def test_idle_hand_reposition_is_counted_from_its_last_position(self):
        result = two_hand_motion(
            (0.0, 0.1, 0.2, 0.3),
            ((1,), (5,), (2,), (6,)),
        )
        self.assertEqual(result.total_distance, 2)
        self.assertEqual(result.active_distance, 0)
        self.assertEqual(result.idle_reposition_distance, 2)
        self.assertEqual(result.free_hand_takeovers, 3)
        self.assertEqual(
            sum(item.idle_reposition_distance for item in result.assignments),
            result.idle_reposition_distance,
        )
        self.assertEqual(
            sum(item.free_hand_takeover for item in result.assignments),
            result.free_hand_takeovers,
        )

    def test_parallel_double_sweep_moves_both_hands(self):
        result = two_hand_motion((0.0, 0.1, 0.2), ((1, 5), (2, 6), (3, 7)))
        self.assertEqual(result.total_distance, 4)
        self.assertEqual(result.active_distance, 4)
        self.assertEqual(result.idle_reposition_distance, 0)
        self.assertEqual(result.fast_jump_violations, 0)

    def test_impossible_fast_jump_is_reported_when_both_hands_were_busy(self):
        result = two_hand_motion((0.0, 0.05), ((1, 5), (3, 7)))
        self.assertEqual(result.fast_jump_violations, 2)
        self.assertEqual(result.total_distance, 4)


if __name__ == "__main__":
    unittest.main()
