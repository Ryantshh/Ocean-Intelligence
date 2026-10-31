"""The search refuses what cannot answer the question, and a month means an overlap (no database required)."""

import asyncio
import unittest
from datetime import date

from ai_platform.backend import tables, tools


def check(**request):
    parsed = tables.Search(**request)
    return parsed, tools._check_search("cargoes", parsed.cargoes) + tools._check_search("vessels", parsed.vessels)


class EmptySearchTests(unittest.TestCase):
    def test_a_table_with_no_filter_is_refused(self):
        _, problems = check(cargoes={}, vessels={"parent_zone": ["Pacific"]})
        self.assertEqual(len(problems), 1)
        self.assertIn("cargoes search sets no filter", problems[0])

    def test_flags_alone_are_no_filter(self):
        _, problems = check(vessels={"include_history": True})
        self.assertIn("vessels search sets no filter", problems[0])

    def test_a_table_left_out_is_not_searched(self):
        _, problems = check(vessels={"parent_zone": ["Pacific"]})
        self.assertEqual(problems, [])

    def test_the_tool_raises_before_any_query(self):
        with self.assertRaises(tools.SearchRejected):
            asyncio.run(tools.search_orders_and_tonnage.ainvoke({"cargoes": {}}))


class NameTests(unittest.TestCase):
    def test_a_port_put_in_a_zone_field_names_the_right_field(self):
        _, problems = check(cargoes={"load_port": ["Madeira"], "discharge_parent_zone": ["North China"]})
        self.assertEqual(len(problems), 1)
        self.assertIn('"North China" is not a zone', problems[0])
        self.assertIn("discharge_port", problems[0])

    def test_a_name_stored_nowhere_is_refused(self):
        _, problems = check(vessels={"parent_zone": ["East Coast South Africa"]})
        self.assertIn("stored nowhere", problems[0])
        _, problems = check(cargoes={"load_port": ["Ponta da Madeira"]})
        self.assertIn("stored nowhere", problems[0])

    def test_stored_names_are_respelled_as_stored(self):
        parsed, problems = check(vessels={"parent_zone": ["east coast south america"], "vessel_status": "anchored"})
        self.assertEqual(problems, [])
        self.assertEqual(parsed.vessels.parent_zone, ["East Coast South America"])
        self.assertEqual(parsed.vessels.vessel_status, "Anchored")

    def test_an_unknown_cargo_type_is_refused(self):
        _, problems = check(cargoes={"cargo_type": "UNOBTANIUM"})
        self.assertIn("not a stored cargo type", problems[0])


class MonthTests(unittest.TestCase):
    def test_a_laycan_month_is_the_overlap_and_replaces_other_bounds(self):
        search = tables.OrderSearch(laycan_month="2025-03", laycan_start_from=date(2025, 3, 1))
        self.assertEqual(search.laycan_end_from, date(2025, 3, 1))
        self.assertEqual(search.laycan_start_to, date(2025, 3, 31))
        self.assertIsNone(search.laycan_start_from)
        self.assertIsNone(search.laycan_end_to)

    def test_an_open_month_handles_february_and_leap_years(self):
        search = tables.VesselSearch(open_month="2028-02")
        self.assertEqual(search.open_end_from, date(2028, 2, 1))
        self.assertEqual(search.open_start_to, date(2028, 2, 29))

    def test_a_malformed_month_is_rejected(self):
        with self.assertRaises(ValueError):
            tables.OrderSearch(laycan_month="2025-13")


if __name__ == "__main__":
    unittest.main()
