"""Exercise the HTTP route with fixed clocks; database access is mocked."""

import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

import asyncpg
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_platform.app.api import dashboard


class ChangeFeedApiTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(dashboard.router)
        self.client = TestClient(app)

    def check_window(
        self, window, tonnage_now, orders_now, tonnage_bounds, orders_bounds
    ):
        references = [{"tonnage_now": tonnage_now, "orders_now": orders_now}]
        rows = [[{"marker": category}] for category in range(4)]
        vessel_override = {"kind": "vessel", "simulated_at": "2025-09-15 10:00:00+00:00"}
        order_override = {"kind": "order", "simulated_at": "2025-09-15 11:00:00+00:00"}
        run = AsyncMock(side_effect=[references, *rows, [vessel_override], [order_override]])
        with patch.object(dashboard, "_run", run):
            response = self.client.get(f"/api/dashboard/changes?window={window}")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        for source, bounds in [("tonnage", tonnage_bounds), ("orders", orders_bounds)]:
            self.assertEqual(data[source + "_since"], bounds[0].isoformat())
            self.assertEqual(data[source + "_until"], bounds[1].isoformat())
        calls = run.call_args_list[1:]
        self.assertEqual(len(calls), 6)
        for call, bounds in zip(
            calls,
            [tonnage_bounds] * 3 + [orders_bounds, tonnage_bounds, orders_bounds],
            strict=True,
        ):
            self.assertEqual(call.args[1], list(bounds))
        for key, expected in zip(
            ["new_vessels", "vessel_status_changes", "field_changes", "new_orders"],
            rows,
            strict=True,
        ):
            self.assertEqual(data[key], expected)
        # Vessel and order overrides merge into one list, newest first.
        self.assertEqual(data["overrides"], [order_override, vessel_override])

    def test_missing_audit_table_empties_only_its_overrides(self):
        references = [{"tonnage_now": "2025-09-16T14:30:00+00:00", "orders_now": "2025-09-16T14:30:00+00:00"}]
        missing = dashboard.HTTPException(status_code=502, detail="missing")
        missing.__cause__ = asyncpg.UndefinedTableError("relation does not exist")
        # A bare date is how json_safe sends a midnight timestamp; it must
        # still sort against the timestamped rows.
        midnight = {"kind": "order", "simulated_at": "2025-09-16"}
        later = {"kind": "order", "simulated_at": "2025-09-16 09:00:00+00:00"}
        run = AsyncMock(side_effect=[references, [], [], [], [], missing, [midnight, later]])
        with patch.object(dashboard, "_run", run):
            response = self.client.get("/api/dashboard/changes?window=dod")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["overrides"], [later, midnight])

    def test_other_audit_failures_still_fail_the_feed(self):
        references = [{"tonnage_now": "2025-09-16T14:30:00+00:00", "orders_now": "2025-09-16T14:30:00+00:00"}]
        broken = dashboard.HTTPException(status_code=502, detail="connection lost")
        run = AsyncMock(side_effect=[references, [], [], [], [], broken, []])
        with patch.object(dashboard, "_run", run):
            response = self.client.get("/api/dashboard/changes?window=dod")
        self.assertEqual(response.status_code, 502)

    def test_monday_route_uses_friday_for_all_categories(self):
        self.check_window(
            "dod",
            "2025-09-15T14:30:00+00:00",
            "2025-09-15T14:30:00+00:00",
            (datetime(2025, 9, 12, 14, 30), datetime(2025, 9, 15, 14, 30)),
            (datetime(2025, 9, 12, 14, 30), datetime(2025, 9, 15, 14, 30)),
        )

    def test_last_24_hours_route_on_a_monday_stays_24_hours(self):
        self.check_window(
            "24h",
            "2025-09-15T14:30:00+00:00",
            "2025-09-15T14:30:00+00:00",
            (datetime(2025, 9, 14, 14, 30), datetime(2025, 9, 15, 14, 30)),
            (datetime(2025, 9, 14, 14, 30), datetime(2025, 9, 15, 14, 30)),
        )

    def test_weekly_route_keeps_independent_source_clocks(self):
        self.check_window(
            "wow",
            "2025-09-16T14:30:00+00:00",
            "2025-09-17T09:00:00+00:00",
            (datetime(2025, 9, 9), datetime(2025, 9, 16)),
            (datetime(2025, 9, 10), datetime(2025, 9, 17)),
        )

    def test_invalid_window_rejected_before_database_access(self):
        with patch.object(dashboard, "_run", AsyncMock()) as run:
            response = self.client.get("/api/dashboard/changes?window=monthly")
        self.assertEqual(response.status_code, 422)
        run.assert_not_called()
