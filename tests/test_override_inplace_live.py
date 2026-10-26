"""Live, rolled-back check that both override forms UPDATE one row in place.

Opt-in (``OI_LIVE_ROLLBACK=1``): connects to the real database, runs the same
SQL the two override forms submit inside a transaction, prints the row before
and after, asserts nothing was inserted or deleted and the key is unchanged,
then ROLLS BACK -- nothing is ever committed.
"""

import os
import unittest

import asyncpg

from ai_platform.backend.db import get_dsn
from ai_platform.order_override import order_override_queries as ooq
from ai_platform.trader_override import trader_override_queries as toq


def _show(label: str, row) -> None:
    print(f"\n{label}:")
    for key, value in dict(row).items():
        print(f"  {key:18} {value}")


@unittest.skipUnless(os.environ.get("OI_LIVE_ROLLBACK") == "1", "set OI_LIVE_ROLLBACK=1")
class InPlaceOverrideTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.conn = await asyncpg.connect(get_dsn(), statement_cache_size=0)
        self.tx = self.conn.transaction()
        await self.tx.start()

    async def asyncTearDown(self):
        await self.tx.rollback()
        await self.conn.close()

    async def test_tonnage_form_updates_in_place(self):
        c = self.conn
        base = await c.fetchrow(
            "SELECT tonnage_row_key, vessel_id, commercial_status, open_area, open_date_start, "
            "open_date_end, update_date, first_date_received, destination "
            "FROM public.tonnage_test WHERE open_area IS NOT NULL LIMIT 1"
        )
        count_before = await c.fetchval("SELECT count(*) FROM public.tonnage_test")
        _show("TONNAGE BEFORE", base)

        new_status = "FIXED" if base["commercial_status"] != "FIXED" else "OPEN"
        new_area = await c.fetchval(
            "SELECT open_area FROM public.tonnage_test WHERE open_area <> $1 LIMIT 1", base["open_area"]
        )
        # What the form submits: status + open area; the dates are left blank.
        sql, params = toq.override_tonnage_row_sql(
            base["tonnage_row_key"], base["vessel_id"], new_status, open_area=new_area,
        )
        returned = await c.fetch(sql, *params)
        after = await c.fetchrow(
            "SELECT tonnage_row_key, vessel_id, commercial_status, open_area, open_date_start, "
            "open_date_end, update_date, first_date_received, destination "
            "FROM public.tonnage_test WHERE tonnage_row_key = $1", base["tonnage_row_key"],
        )
        _show("TONNAGE AFTER (same tonnage_row_key)", after)

        self.assertEqual(len(returned), 1)
        self.assertEqual(await c.fetchval("SELECT count(*) FROM public.tonnage_test"), count_before)
        self.assertEqual(after["commercial_status"], new_status)
        self.assertEqual(after["open_area"], new_area)
        for untouched in ("open_date_start", "open_date_end", "first_date_received", "destination"):
            self.assertEqual(after[untouched], base[untouched], untouched)

    async def test_order_form_updates_in_place(self):
        c = self.conn
        cols = ("order_id::text AS order_id, order_seq, cargo_type, laycan_start, laycan_end, "
                "cargo_weight_min, cargo_weight_max, update_date")
        base = await c.fetchrow(
            f"SELECT {cols} FROM public.order_test WHERE laycan_start IS NOT NULL "
            "AND cargo_weight_min IS NOT NULL LIMIT 1"
        )
        count_before = await c.fetchval("SELECT count(*) FROM public.order_test")
        _show("ORDER BEFORE", base)

        # What the form submits: new min weight only; laycan and max left blank.
        new_min = float(base["cargo_weight_min"]) + 1000
        sql, params = ooq.update_order_sql(
            base["order_id"], base["order_seq"],
            laycan_start=None, laycan_end=None,
            cargo_weight_min=new_min, cargo_weight_max=None,
        )
        returned = await c.fetch(sql, *params)
        after = await c.fetchrow(
            f"SELECT {cols} FROM public.order_test WHERE order_id = $1::text::bigint AND order_seq = $2",
            base["order_id"], base["order_seq"],
        )
        _show("ORDER AFTER (same order_id, order_seq)", after)

        self.assertEqual(len(returned), 1)
        self.assertEqual(await c.fetchval("SELECT count(*) FROM public.order_test"), count_before)
        self.assertEqual(float(after["cargo_weight_min"]), new_min)
        for untouched in ("laycan_start", "laycan_end", "cargo_weight_max", "cargo_type"):
            self.assertEqual(after[untouched], base[untouched], untouched)


if __name__ == "__main__":
    unittest.main()
