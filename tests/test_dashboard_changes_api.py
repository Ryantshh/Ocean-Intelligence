"""Exercise the HTTP route with fixed clocks; database access is mocked."""

# The driver requires naive UTC bounds.
# ruff: noqa: DTZ001

import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

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
        run = AsyncMock(side_effect=[references, *rows])
        with patch.object(dashboard, "_run", run):
            response = self.client.get(f"/api/dashboard/changes?window={window}")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        for source, bounds in [("tonnage", tonnage_bounds), ("orders", orders_bounds)]:
            self.assertEqual(data[source + "_since"], bounds[0].isoformat())
            self.assertEqual(data[source + "_until"], bounds[1].isoformat())
        calls = run.call_args_list[1:]
        self.assertEqual(len(calls), 4)
        for call, bounds in zip(
            calls, [tonnage_bounds] * 3 + [orders_bounds], strict=True
        ):
            self.assertEqual(call.args[1], list(bounds))
        for key, expected in zip(
            ["new_vessels", "vessel_status_changes", "field_changes", "new_orders"],
            rows,
            strict=True,
        ):
            self.assertEqual(data[key], expected)

    def test_monday_route_uses_friday_for_all_categories(self):
        self.check_window(
            "dod",
            "2025-09-15T14:30:00+00:00",
            "2025-09-15T14:30:00+00:00",
            (datetime(2025, 9, 12, 14, 30), datetime(2025, 9, 15, 14, 30)),
            (datetime(2025, 9, 12, 14, 30), datetime(2025, 9, 15, 14, 30)),
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
