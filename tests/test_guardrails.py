"""Guardrails: what a reply rests on decides its footer, never the model.

The evidence record reads tool calls and results as ``cl_app`` passes them in,
and the confidence level follows from what was found. ``new_vessels`` answers
only the last fourteen days, with the dashboard's own query, and a flagged
request is logged even when Langfuse is unreachable. Database access is mocked.
"""

import asyncio
import json
import unittest
from datetime import date, datetime, time, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from ai_platform.backend import guardrails, tools

AS_OF = date(2025, 10, 28)


def _midnight(day: date) -> datetime:
    """Naive midnight, as the dashboard query binds its bounds."""
    return datetime.combine(day, time())


def _search(evidence, call_id, args, counts, capped=()):
    evidence.record_call(call_id, "search_orders_and_tonnage", args)
    evidence.record_result(
        call_id,
        json.dumps({"counts": counts, "capped": list(capped), "cargoes": [], "vessels": []}),
    )


class ConfidenceTests(unittest.TestCase):
    def test_exact_search_with_rows_is_high(self):
        evidence = guardrails.Evidence()
        _search(evidence, "1", {"vessels": {"parent_zone": ["ECSA"]}}, {"cargoes": 0, "vessels": 12})
        self.assertEqual(guardrails.confidence(evidence)[0], "High")
        text = guardrails.footer(evidence, AS_OF)
        self.assertIn("vessel positions (12)", text)
        self.assertIn("28 October 2025", text)
        self.assertIn("Confidence: High", text)
        # a table the model did not ask for is not a source
        self.assertNotIn("cargo enquiries", text)

    def test_wording_search_is_medium(self):
        evidence = guardrails.Evidence()
        _search(evidence, "1", {"cargoes": {"cargo_description": "fertiliser in bags"}}, {"cargoes": 7, "vessels": 0})
        self.assertEqual(guardrails.confidence(evidence)[0], "Medium")

    def test_capped_search_is_medium(self):
        evidence = guardrails.Evidence()
        _search(evidence, "1", {"cargoes": {"load_zone": ["ECSA"]}}, {"cargoes": 50, "vessels": 0}, capped=["cargoes"])
        self.assertEqual(guardrails.confidence(evidence)[0], "Medium")

    def test_nothing_found_is_low(self):
        evidence = guardrails.Evidence()
        _search(evidence, "1", {"vessels": {"open_area": ["Narnia"]}}, {"cargoes": 0, "vessels": 0})
        self.assertEqual(guardrails.confidence(evidence), ("Low", "nothing on the book matched"))

    def test_half_empty_two_table_search_is_medium(self):
        evidence = guardrails.Evidence()
        _search(
            evidence,
            "1",
            {"cargoes": {"load_zone": ["ECSA"]}, "vessels": {"parent_zone": ["ECSA"]}},
            {"cargoes": 4, "vessels": 0},
        )
        self.assertEqual(guardrails.confidence(evidence)[0], "Medium")

    def test_failed_tool_is_low(self):
        evidence = guardrails.Evidence()
        evidence.record_call("1", "search_orders_and_tonnage", {"vessels": {}})
        evidence.record_result("1", "That search failed: timeout. Narrow it.", "error")
        self.assertEqual(guardrails.confidence(evidence)[0], "Low")

    def test_cut_off_reply_is_low(self):
        evidence = guardrails.Evidence()
        _search(evidence, "1", {"vessels": {"parent_zone": ["ECSA"]}}, {"cargoes": 0, "vessels": 3})
        self.assertEqual(guardrails.confidence(evidence, cut_off=True)[0], "Low")

    def test_matches_awaiting_input_are_medium(self):
        evidence = guardrails.Evidence()
        evidence.record_call("1", "match_orders", {"order_ids": ["1"]})
        report = "1: 3 vessels matched.\n2 vessels need the trader's input in the results panel."
        evidence.record_result("1", json.dumps({"orders": [{"order_id": "1", "matched": 3, "report": report}]}))
        self.assertEqual(guardrails.confidence(evidence)[0], "Medium")
        self.assertIn("vessel matches (3)", guardrails.footer(evidence, AS_OF))

    def test_no_search_gets_disclaimer(self):
        evidence = guardrails.Evidence()
        self.assertIsNone(guardrails.confidence(evidence))
        self.assertEqual(guardrails.footer(evidence, AS_OF), guardrails.NO_SEARCH_DISCLAIMER)

    def test_clarifying_question_alone_is_not_a_search(self):
        evidence = guardrails.Evidence()
        evidence.record_call("1", "ask_user", {"questions": []})
        evidence.record_result("1", json.dumps({"Which month?": "June"}))
        self.assertEqual(guardrails.footer(evidence, AS_OF), guardrails.NO_SEARCH_DISCLAIMER)

    def test_flagged_request_has_no_footer(self):
        evidence = guardrails.Evidence()
        evidence.record_call("1", "flag_for_dev_team", {"request": "x", "reason": "y"})
        evidence.record_result("1", json.dumps({"flagged": True}))
        self.assertIsNone(guardrails.confidence(evidence))
        self.assertEqual(guardrails.footer(evidence, AS_OF), "")

    def test_out_of_range_new_vessels_has_no_footer(self):
        evidence = guardrails.Evidence()
        evidence.record_call("1", "new_vessels", {"since": "2025-09-01"})
        evidence.record_result("1", json.dumps({"out_of_range": "only the last 14 days"}))
        self.assertEqual(guardrails.footer(evidence, AS_OF), "")


class NewVesselsTests(unittest.TestCase):
    def _run(self, **request):
        rows = [
            {"vessel_id": "VESSEL 0001", "new_as_of": "2025-10-27"},
            {"vessel_id": "VESSEL 0001", "new_as_of": "2025-10-28"},
            {"vessel_id": "VESSEL 0002", "new_as_of": "2025-10-28"},
        ]
        fetch = AsyncMock(return_value=rows)
        with patch.object(tools, "working_date", return_value=AS_OF), patch.object(tools, "fetch_rows", fetch):
            result = asyncio.run(tools.new_vessels.ainvoke(request))
        return result, fetch

    def test_fourteen_days_back_is_answered_with_the_dashboard_query(self):
        since = AS_OF - timedelta(days=tools.NEW_VESSEL_LOOKBACK_DAYS)
        result, fetch = self._run(since=since.isoformat())
        sql, params = fetch.await_args.args
        self.assertIn("vessel_status_history", sql)
        # until is inclusive for the agent and exclusive in the dashboard query
        self.assertEqual(params, [_midnight(date(2025, 10, 14)), _midnight(date(2025, 10, 29))])
        self.assertEqual(result["counts"], {"vessels": 2})
        self.assertEqual(result["by_day"], {"2025-10-27": 1, "2025-10-28": 2})

    def test_fifteen_days_back_is_out_of_range(self):
        since = AS_OF - timedelta(days=tools.NEW_VESSEL_LOOKBACK_DAYS + 1)
        result, fetch = self._run(since=since.isoformat())
        self.assertIn("out_of_range", result)
        self.assertIn("flag_for_dev_team", result["out_of_range"])
        fetch.assert_not_awaited()

    def test_until_after_today_is_clamped(self):
        _, fetch = self._run(since="2025-10-27", until="2025-11-30")
        self.assertEqual(fetch.await_args.args[1][1], _midnight(date(2025, 10, 29)))

    def test_zone_and_size_filter_the_dashboard_query_in_sql(self):
        _, fetch = self._run(since="2025-10-27", parent_zone=["East Coast South America"], dwt_min=180000)
        sql, params = fetch.await_args.args
        self.assertTrue(sql.startswith("SELECT * FROM (WITH events AS"))
        self.assertIn("zone = ANY($3::text[])", sql)
        self.assertIn("nv.dwt >= $4", sql)
        self.assertEqual(params[2:], [["East Coast South America"], 180000])

    def test_no_filter_leaves_the_dashboard_query_as_is(self):
        _, fetch = self._run(since="2025-10-27")
        sql, params = fetch.await_args.args
        self.assertNotIn("AS nv", sql)
        self.assertEqual(len(params), 2)


class FlagForDevTeamTests(unittest.TestCase):
    def test_scores_the_trace(self):
        client = MagicMock()
        with patch.object(tools, "get_client", return_value=client):
            result = tools.flag_for_dev_team.invoke({"request": "freight rates", "reason": "not on the book"})
        self.assertEqual(result, {"flagged": True})
        kwargs = client.score_current_trace.call_args.kwargs
        self.assertEqual(kwargs["name"], tools.UNSUPPORTED_REQUEST_SCORE)
        self.assertEqual(kwargs["data_type"], "BOOLEAN")

    def test_logs_and_survives_a_langfuse_failure(self):
        with (
            patch.object(tools, "get_client", side_effect=RuntimeError("no langfuse")),
            self.assertLogs("agent", level="WARNING") as logs,
        ):
            result = tools.flag_for_dev_team.invoke({"request": "past fixtures", "reason": "not held"})
        self.assertEqual(result, {"flagged": True})
        self.assertTrue(any("past fixtures" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
