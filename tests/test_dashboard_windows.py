"""Regression coverage for dashboard comparison periods (no database required)."""

# Naive UTC timestamps are the database binding contract under test.
# ruff: noqa: DTZ001

import unittest
from datetime import UTC, datetime, timedelta, timezone

from ai_platform.backend import dashboard_queries as dq


class ChangeWindowTests(unittest.TestCase):
    def test_monday_compares_to_friday_at_same_time(self):
        now = datetime(2025, 9, 15, 14, 30, tzinfo=UTC)
        self.assertEqual(
            dq.change_window_bounds("dod", now=now),
            (datetime(2025, 9, 12, 14, 30), datetime(2025, 9, 15, 14, 30)),
        )

    def test_each_day_uses_previous_weekday(self):
        for day, expected_day in [
            (15, 12),
            (16, 15),
            (17, 16),
            (18, 17),
            (19, 18),
            (20, 19),
            (21, 19),
        ]:
            with self.subTest(day=day):
                start, end = dq.change_window_bounds(
                    "dod", now=datetime(2025, 9, day, 10)
                )
                self.assertEqual(start, datetime(2025, 9, expected_day, 10))
                self.assertEqual(end, datetime(2025, 9, day, 10))

    def test_tuesday_week_covers_last_tuesday_through_monday(self):
        start, end = dq.change_window_bounds(
            "wow", now=datetime(2025, 9, 16, 14, 30, tzinfo=UTC)
        )
        self.assertEqual(start, datetime(2025, 9, 9))
        self.assertEqual(end, datetime(2025, 9, 16))
        self.assertTrue(start <= datetime(2025, 9, 15, 23, 59, 59) < end)
        self.assertFalse(start <= datetime(2025, 9, 16, 0, 0, 1) < end)

    def test_week_is_seven_full_days_across_year_boundary(self):
        for day in range(1, 8):
            start, end = dq.change_window_bounds(
                "wow", now=datetime(2026, 1, day, 23, 59)
            )
            self.assertEqual(end - start, timedelta(days=7))
            self.assertEqual(end, datetime(2026, 1, day))

    def test_daily_window_crosses_month_boundary(self):
        start, _ = dq.change_window_bounds("dod", now=datetime(2025, 9, 1, 9))
        self.assertEqual(start, datetime(2025, 8, 29, 9))

    def test_aware_reference_is_converted_to_utc(self):
        start, end = dq.change_window_bounds(
            "wow",
            now=datetime(2025, 9, 16, 1, tzinfo=timezone(timedelta(hours=8))),
        )
        self.assertEqual(start, datetime(2025, 9, 8))
        self.assertEqual(end, datetime(2025, 9, 15))

    def test_all_feed_queries_bind_both_window_edges(self):
        start, end = dq.change_window_bounds("wow", now=datetime(2025, 9, 16, 12))
        for build in [
            dq.new_vessels_sql,
            dq.vessel_status_changes_sql,
            dq.vessel_field_changes_sql,
            dq.new_orders_sql,
        ]:
            with self.subTest(query=build.__name__):
                sql, params = build(start, end)
                self.assertEqual(params, [start, end])
                self.assertIn(">= $1", sql)
                self.assertIn("< $2", sql)

    def test_invalid_window_is_rejected(self):
        with self.assertRaises(ValueError):
            dq.change_window_bounds("monthly")


if __name__ == "__main__":
    unittest.main()
