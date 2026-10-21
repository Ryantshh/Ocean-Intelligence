"""Regression checks for override validation and single-row update requests."""
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from ai_platform.order_override import order_override as orders
from ai_platform.order_override import order_override_queries as oq
from ai_platform.trader_override import trader_override as tonnage
from ai_platform.trader_override import trader_override_queries as tq


class OverrideFieldsTests(unittest.IsolatedAsyncioTestCase):
    def order(self, **changes):
        return orders.OrderOverrideRequest(
            **dict(order_id="123", order_seq=2, entered_by=" Trader ", **changes)
        )

    def test_invalid_inputs(self):
        for changes in [{"laycan_start": "2026-02-30"}, {"cargo_weight_min": float("inf")},
                        {"cargo_weight_max": -1}]:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.order(**changes)
        for field in ("entered_by", "reason"):
            values = self.order().model_dump()
            values[field] = "   "
            with self.assertRaises(ValidationError):
                orders.OrderOverrideRequest(**values)

    async def test_partial_order_range_rejected_before_write(self):
        for changes, stored in [
            ({"cargo_weight_min": 100}, {"cargo_weight_max": 50}),
            ({"laycan_end": "2026-01-01"}, {"laycan_start": "2026-02-01"}),
        ]:
            with patch.object(orders, "_run", AsyncMock(return_value=[stored])) as query:
                with self.assertRaises(HTTPException) as error:
                    await orders.submit_order_override(self.order(**changes))
                self.assertEqual(error.exception.status_code, 422)
                self.assertEqual(query.await_count, 1)

    async def test_customer_saved_only_in_existing_audit_field(self):
        before = {"laycan_start": None, "laycan_end": None, "cargo_weight_min": 10, "cargo_weight_max": 20}
        after = dict(before)
        with patch.object(orders, "_run", AsyncMock(side_effect=[[before], [after]])) as query, \
             patch.object(orders, "fetch_rows", AsyncMock(return_value=[])) as audit:
            result = await orders.submit_order_override(self.order(customer_name="Acme"))
        sql, params = query.await_args.args
        self.assertIn("AND order_seq = $6", sql)
        self.assertEqual(params[5:], [2])
        self.assertNotIn("customer_name", sql)
        self.assertNotIn("reason", sql)
        self.assertTrue(result["audit_saved"])
        self.assertEqual(audit.await_args.args[1][-2:], ["Acme", "Trader"])

    async def test_missing_audit_reports_unsaved_customer(self):
        before = {"laycan_start": None, "laycan_end": None, "cargo_weight_min": 10, "cargo_weight_max": 20}
        with patch.object(orders, "_run", AsyncMock(side_effect=[[before], [before]])), \
             patch.object(orders, "fetch_rows", AsyncMock(side_effect=orders.asyncpg.UndefinedTableError())), \
             patch.object(orders.logger, "warning"):
            result = await orders.submit_order_override(self.order(customer_name="Acme"))
        self.assertFalse(result["audit_saved"])

    async def test_partial_tonnage_dates_rejected_before_write(self):
        body = tonnage.OverrideRequest(vessel_id="V", base_tonnage_row_key="row", override_status="OPEN",
                                       entered_by="Trader", open_date_start="2026-03-01")
        with patch.object(tonnage, "_run", AsyncMock(side_effect=[[{}], [{"open_date_end": "2026-02-01"}]])) as query:
            with self.assertRaises(HTTPException):
                await tonnage.submit_override(body)
            self.assertEqual(query.await_count, 2)

    async def test_tonnage_audit_result_is_returned(self):
        body = tonnage.OverrideRequest(vessel_id="V", base_tonnage_row_key="row",
                                       override_status="OPEN", entered_by="Trader")
        row = {"commercial_status": "OPEN", "open_area": None,
               "open_date_start": None, "open_date_end": None}
        for audit_result, expected in [([], True), (HTTPException(502, "Unavailable"), False)]:
            with patch.object(tonnage, "_run", AsyncMock(side_effect=[[{}], [row], [row], audit_result])), \
                 patch.object(tonnage.logger, "warning"):
                result = await tonnage.submit_override(body)
            self.assertEqual(result["audit_saved"], expected)

    def test_queries_preserve_identity_and_parameterize_metadata(self):
        sql, params = tq.override_tonnage_row_sql("row", "V", "OPEN", open_area="Don't interpolate")
        self.assertIn("WHERE tonnage_row_key = $1 AND vessel_id = $2", sql)
        self.assertNotIn("Don't interpolate", sql)
        self.assertEqual(params[3], "Don't interpolate")
        for sql, params in [tq.insert_audit_sql("V", "OPEN", "Trader"),
                            oq.insert_audit_sql("123", 2, "Trader")]:
            columns = sql.split("(", 1)[1].split(")", 1)[0].split(", ")
            self.assertEqual(len(columns), len(set(columns)))
            self.assertEqual(len(columns), len(params))
            self.assertEqual(params[-1], "Trader")


if __name__ == "__main__":
    unittest.main()
