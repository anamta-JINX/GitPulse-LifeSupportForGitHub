from __future__ import annotations

import unittest

from gitpulse.cloud_schedule import due_pulse_count, parse_hhmm, random_schedule_times


class CloudScheduleTests(unittest.TestCase):
    def test_two_times_are_stable_unique_and_inside_window(self) -> None:
        kwargs = dict(
            date_key="2026-09-25",
            repo_id="private-cloud",
            repo_url="https://github.com/anamta-JINX/Private.git",
            count=2,
            start_time="10:00",
            end_time="21:59",
        )
        first = random_schedule_times(**kwargs)
        second = random_schedule_times(**kwargs)

        self.assertEqual(first, second)
        self.assertEqual(len(first), 2)
        self.assertEqual(len(set(first)), 2)
        self.assertTrue(all(parse_hhmm("10:00") <= parse_hhmm(value) <= parse_hhmm("21:59") for value in first))

    def test_next_date_gets_a_different_schedule(self) -> None:
        common = dict(
            repo_id="private-cloud",
            repo_url="https://github.com/anamta-JINX/Private.git",
            count=2,
            start_time="10:00",
            end_time="21:59",
        )
        first = random_schedule_times(date_key="2026-09-25", **common)
        second = random_schedule_times(date_key="2026-09-26", **common)
        self.assertNotEqual(first, second)

    def test_two_pulses_are_spread_across_buckets(self) -> None:
        values = random_schedule_times(
            date_key="2026-09-25",
            repo_id="bucket-test",
            repo_url="https://github.com/example/private.git",
            count=2,
            start_time="10:00",
            end_time="21:59",
        )
        midpoint = parse_hhmm("16:00")
        self.assertLess(parse_hhmm(values[0]), midpoint)
        self.assertGreaterEqual(parse_hhmm(values[1]), midpoint)

    def test_due_count_tracks_clock(self) -> None:
        planned = ["11:15", "19:42"]
        self.assertEqual(due_pulse_count(planned, "10:00"), 0)
        self.assertEqual(due_pulse_count(planned, "11:15"), 1)
        self.assertEqual(due_pulse_count(planned, "19:41"), 1)
        self.assertEqual(due_pulse_count(planned, "19:42"), 2)

    def test_invalid_or_too_small_windows_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            random_schedule_times("2026-09-25", "x", "https://github.com/x/y", 2, "22:00", "10:00")
        with self.assertRaises(ValueError):
            random_schedule_times("2026-09-25", "x", "https://github.com/x/y", 3, "10:00", "10:01")


if __name__ == "__main__":
    unittest.main()
