"""Orders are (order_id, order_seq): twins sharing an order_id stay apart.

order_id is a hash of ten content fields, so identical orders share one and
are numbered apart by order_seq. Whether they are one order or two is the
trader's call, so the override form must read and update exactly the
order_seq selected, and every order list must say when a row has twins.
Database access is mocked, so these pin the SQL text and the API wiring.
"""

import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_platform.backend import dashboard_queries as dq
from ai_platform.order_override import order_override
from ai_platform.order_override import order_override_queries as ooq

SINCE = datetime(2025, 10, 1)
UNTIL = datetime(2025, 10, 8)


class OrderOverrideQueryTests(unittest.TestCase):
    def test_detail_selects_one_order_seq(self):
        sql, params = ooq.order_detail_sql("728280593490835944", 2)
        self.assertIn("order_seq = $2", sql)
        self.assertIn("order_seq_count", sql)
        self.assertEqual(params, ["728280593490835944", 2])

    def test_update_touches_only_the_selected_order_seq(self):
        sql, params = ooq.update_order_sql(
            "728280593490835944", 2,
            laycan_start=None, laycan_end=None, cargo_weight_min=None, cargo_weight_max=1.0,
        )
        self.assertIn("WHERE order_id = $1::text::bigint AND order_seq = $6", sql)
        self.assertEqual(params[0], "728280593490835944")
        self.assertEqual(params[5], 2)

    def test_audit_records_order_seq(self):
        sql, params = ooq.insert_audit_sql("1", 3, "trader", reason="broker update")
        self.assertIn("order_id, order_seq,", sql)
        self.assertEqual(params[:2], ["1", 3])
        self.assertIn("trader", params)

    def test_picker_lists_each_order_seq_with_its_count(self):
        sql, _ = ooq.orders_sql()
        self.assertIn("order_seq", sql)
        self.assertIn("count(*) OVER (PARTITION BY order_id) AS order_seq_count", sql)


class OrderOverrideApiTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(order_override.router)
        self.client = TestClient(app)

    def test_detail_route_takes_order_seq(self):
        run = AsyncMock(return_value=[{"order_id": "5", "order_seq": 2}])
        with patch.object(order_override, "_run", run):
            response = self.client.get("/api/order-override/orders/5/2")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(run.call_args.args[1], ["5", 2])

    def test_submit_requires_order_seq(self):
        response = self.client.post(
            "/api/order-override", json={"order_id": "5", "entered_by": "trader"}
        )
        self.assertEqual(response.status_code, 422)

    def test_submit_updates_the_order_seq_given(self):
        snapshot = {
            "order_id": "5", "order_seq": 2, "laycan_start": None, "laycan_end": None,
            "cargo_weight_min": None, "cargo_weight_max": None,
        }
        run = AsyncMock(side_effect=[[snapshot], [snapshot]])
        audit = AsyncMock(return_value=[{}])
        with patch.object(order_override, "_run", run), patch.object(order_override, "fetch_rows", audit):
            response = self.client.post(
                "/api/order-override",
                json={
                    "order_id": "5", "order_seq": 2, "entered_by": "trader",
                    "reason": "broker update", "cargo_weight_max": 9,
                },
            )
        self.assertEqual(response.status_code, 201)
        detail_call, update_call = run.call_args_list
        self.assertEqual(detail_call.args[1], ["5", 2])
        self.assertIn("AND order_seq = $6", update_call.args[0])
        self.assertEqual(update_call.args[1][5], 2)
        self.assertEqual(audit.call_args.args[1][:2], ["5", 2])


class DashboardOrderListTests(unittest.TestCase):
    def test_tracker_pages_stably_and_counts_twins(self):
        sql, _ = dq.orders_sql(None, "date_received", None, 25, 0)
        self.assertIn("order_seq_count", sql)
        self.assertIn("count(*) OVER (PARTITION BY order_id) AS order_seq_count", sql)
        self.assertTrue(sql.split("ORDER BY")[-1].strip().startswith("date_received DESC NULLS LAST, order_id, order_seq"))

    def test_twins_counted_before_the_list_filters(self):
        # The count sits inside the source subquery, so a list narrowed to one
        # day or page still reports twins outside it.
        for sql, _ in (dq.new_orders_sql(SINCE, UNTIL), dq.orders_on_day_sql(SINCE.date())):
            self.assertIn(dq._ORDERS_SOURCE, sql)
            self.assertIn("order_seq", sql.split("FROM")[0])

    def test_definition_is_recorded_in_readme(self):
        readme = (Path(dq.__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
        self.assertIn("an order is `(order_id, order_seq)`, and order counts include every `order_seq`", readme)


if __name__ == "__main__":
    unittest.main()
