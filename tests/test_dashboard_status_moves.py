"""The Status Changes chart: plus/minus supply moves dated by effective day.

Definition under test (see ``_STATUS_MOVES_CTE`` in dashboard_queries.py):
FIXED/ON SUBS -> OPEN is +1, OPEN -> FIXED/ON SUBS is -1, FIXED <-> ON SUBS
is 0, and a move is dated by the day the new status takes effect. Database
access is mocked, so these pin the SQL text and the API wiring; the numbers
themselves were checked against a Python recomputation on the live data.
"""

# The driver requires naive UTC bounds.
# ruff: noqa: DTZ001

import unittest
from datetime import date, datetime
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_platform.app.api import dashboard
from ai_platform.backend import dashboard_queries as dq

SINCE = datetime(2025, 9, 23, 2, 28)
UNTIL = datetime(2025, 10, 6, 2, 28)


class StatusMoveDefinitionTests(unittest.TestCase):
    def test_classification_treats_on_subs_as_fixed(self):
        cte = dq._STATUS_MOVES_CTE
        self.assertIn("status = 'OPEN' AND prev_status IN ('FIXED', 'ON SUBS') THEN 'up'", cte)
        self.assertIn("prev_status = 'OPEN' AND status IN ('FIXED', 'ON SUBS') THEN 'down'", cte)

    def test_fixed_to_on_subs_counts_for_neither_side(self):
        # Anything that is neither branch of the CASE is NULL, then dropped.
        self.assertIn("WHERE direction IS NOT NULL", dq._STATUS_MOVES_CTE)

    def test_dated_by_effective_day_not_report_day(self):
        cte = dq._STATUS_MOVES_CTE
        self.assertIn("open_date_start::date AS day", cte)
        self.assertNotIn("update_date", cte)

    def test_first_segment_is_a_baseline_and_gaps_are_not_changes(self):
        cte = dq._STATUS_MOVES_CTE
        self.assertIn("NOT is_first_segment", cte)
        self.assertIn("status IS DISTINCT FROM prev_status", cte)

    def test_daily_totals_and_region_split_share_one_definition(self):
        for builder in (dq.daily_status_changes_sql, dq.daily_status_changes_by_region_sql):
            sql, params = builder(SINCE, UNTIL)
            self.assertIn(dq._STATUS_MOVES_CTE, sql)
            self.assertEqual(params, [SINCE, UNTIL])

    def test_nothing_effective_at_or_after_now_is_counted(self):
        sql, _ = dq.daily_status_changes_sql(SINCE, UNTIL)
        self.assertIn("day::timestamp < $2::timestamp", sql)

    def test_daily_totals_return_up_down_and_gross(self):
        sql, _ = dq.daily_status_changes_sql(SINCE, UNTIL)
        for column in ("AS up", "AS down", "AS count"):
            self.assertIn(column, sql)


class ClickThroughTests(unittest.TestCase):
    def sql_for(self, **kwargs):
        sql, params = dq.vessels_on_day_sql("status_changes", date(2025, 10, 5), **kwargs)
        return sql, params

    def test_direction_narrows_to_one_bar(self):
        up_sql, _ = self.sql_for(direction="up")
        down_sql, _ = self.sql_for(direction="down")
        both_sql, _ = self.sql_for()
        self.assertIn("direction = 'up'", up_sql)
        self.assertIn("direction = 'down'", down_sql)
        self.assertNotIn("direction = '", both_sql)

    def test_unknown_direction_is_never_interpolated(self):
        hostile = "up' OR '1'='1"
        sql, _ = self.sql_for(direction=hostile)
        self.assertNotIn("OR '1'='1", sql)
        self.assertNotIn("direction = '", sql)

    def test_range_since_does_not_clip_a_whole_day_status_bar(self):
        # range_since carries the time of day; clipping to it would drop the
        # earlier part of the first bar, which is a whole calendar day.
        _, params = self.sql_for(range_since=datetime(2025, 10, 5, 14, 0))
        self.assertEqual(params[0], datetime(2025, 10, 5, 0, 0))

    def test_range_until_still_stops_the_last_bar_at_now(self):
        _, params = self.sql_for(range_until=datetime(2025, 10, 5, 9, 0))
        self.assertEqual(params[1], datetime(2025, 10, 5, 9, 0))

    def test_new_vessels_clipping_is_unchanged(self):
        _, params = dq.vessels_on_day_sql(
            "new_vessels", date(2025, 10, 5), range_since=datetime(2025, 10, 5, 14, 0)
        )
        self.assertEqual(params[0], datetime(2025, 10, 5, 14, 0))


class DailyCountsApiTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(dashboard.router)
        self.client = TestClient(app)

    def test_regions_carry_up_and_down_separately(self):
        day = "2025-10-05"
        references = [{"tonnage_now": "2025-10-06T02:28:00+00:00", "orders_now": "2025-10-06T02:28:00+00:00"}]
        totals = [{"day": day, "up": 3, "down": 2, "count": 5}]
        by_region = [
            {"day": day, "region": "Far East", "up": 2, "down": 0, "count": 2},
            {"day": day, "region": "South Africa", "up": 1, "down": 2, "count": 3},
        ]
        empty = [{"day": day, "count": 0}]
        run = AsyncMock(
            side_effect=[references, empty, totals, empty, [], by_region, []]
        )
        with patch.object(dashboard, "_run", run):
            response = self.client.get("/api/dashboard/daily-counts?days=1")
        self.assertEqual(response.status_code, 200)
        row = response.json()["status_changes"][0]
        self.assertEqual((row["up"], row["down"], row["count"]), (3, 2, 5))
        self.assertEqual(
            row["regions"],
            [
                {"region": "Far East", "up": 2, "down": 0, "count": 2},
                {"region": "South Africa", "up": 1, "down": 2, "count": 3},
            ],
        )

    def test_direction_is_forwarded_to_the_query(self):
        run = AsyncMock(return_value=[])
        with patch.object(dashboard, "_run", run):
            response = self.client.get(
                "/api/dashboard/vessels/by-day",
                params={"metric": "status_changes", "day": "2025-10-05", "direction": "down"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("direction = 'down'", run.call_args.args[0])

    def test_bad_direction_is_rejected_before_database_access(self):
        run = AsyncMock(return_value=[])
        with patch.object(dashboard, "_run", run):
            response = self.client.get(
                "/api/dashboard/vessels/by-day",
                params={"metric": "status_changes", "day": "2025-10-05", "direction": "sideways"},
            )
        self.assertEqual(response.status_code, 422)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
