"""The search refuses what cannot answer the question, and a month means an overlap (no database required)."""

import asyncio
import json
import unittest
from datetime import date, timedelta
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from ai_platform.backend import tables, tools

TODAY = date(2025, 6, 17)
LIVE_BOOK = {"open_end_from": TODAY, "open_start_to": TODAY + timedelta(days=30), "updated_from": TODAY - timedelta(days=5)}


def check(**request):
    parsed = tables.Search(**request)
    return parsed, tools._check_search("cargoes", parsed.cargoes) + tools._check_search("vessels", parsed.vessels)


def unasked(question, **request):
    problems, _ = tools._unasked_conditions(tables.Search(**request), question, TODAY)
    return problems


def form_exchange(question, header, answer, call_id):
    """An ask_user call the model made and the trader's answer to it."""
    call = AIMessage(content="", tool_calls=[{"name": "ask_user", "id": call_id, "args": {
        "questions": [{"question": question, "header": header, "options": [{"label": "x", "description": "y"}]}]}}])
    return [call, ToolMessage(content=json.dumps({question: answer}), tool_call_id=call_id)]


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
        runtime = SimpleNamespace(state={"messages": [HumanMessage("Show me the cargoes")]})
        with self.assertRaises(tools.SearchRejected):
            asyncio.run(tools.search_orders_and_tonnage.coroutine(runtime=runtime, cargoes=tables.OrderSearch()))


class UnaskedConditionTests(unittest.TestCase):
    def test_a_status_nobody_asked_for_is_refused(self):
        for question in ("Show me the ECSA ballasters.", "Show me the WAF openers."):
            problems = unasked(question, vessels={"parent_zone": ["West Africa"], "commercial_status": "OPEN"})
            self.assertEqual(len(problems), 1, question)
            self.assertIn("no fixture status", problems[0])

    def test_a_status_the_trader_asked_for_is_kept(self):
        for question, status in (
            ("Show me open vessels in the Far East of at least 180,000 dwt.", "OPEN"),
            ("Which vessels are fixed?", "FIXED"),
            ("Which vessels are on subs?", "ON SUBS"),
            ("Which vessels open in West Australia were updated in the last 3 days?", "OPEN"),
        ):
            self.assertEqual(unasked(question, vessels={"commercial_status": status}), [], question)

    def test_the_live_book_window_is_refused_when_nothing_asks_for_it(self):
        problems = unasked("Which vessels are fixed?", vessels={"commercial_status": "FIXED", **LIVE_BOOK})
        self.assertEqual(len(problems), 1)
        self.assertIn("not a list question", problems[0])

    def test_a_list_question_keeps_the_live_book_window(self):
        for question in ("Show me today's tonnage list in the Far East.", "Show me today's tonnage list open in the Pacific."):
            self.assertEqual(unasked(question, vessels={"parent_zone": ["Far East"], **LIVE_BOOK}), [], question)
        cargo_book = {"laycan_end_from": TODAY, "laycan_start_to": TODAY + timedelta(days=30)}
        self.assertEqual(unasked("Show me today's cargo list.", cargoes=cargo_book), [])

    def test_a_size_is_not_a_time(self):
        question = "Show me open vessels in the Far East of at least 180,000 dwt."
        problems = unasked(question, vessels={"commercial_status": "OPEN", "dwt_min": 180000, **LIVE_BOOK})
        self.assertEqual(len(problems), 1)
        self.assertIn("not a list question", problems[0])

    def test_a_date_is_a_time(self):
        for question in ("Vessels open on 2025-06-20", "Vessels open on 20/6", "Vessels open by 5 July"):
            self.assertEqual(unasked(question, vessels={"parent_zone": ["Far East"], **LIVE_BOOK}), [], question)

    def test_a_condition_refused_once_is_removed_the_second_time(self):
        parsed = tables.Search(vessels={"parent_zone": ["West Africa"], "commercial_status": "OPEN", **LIVE_BOOK})
        problems, removed = tools._unasked_conditions(parsed, "Show me the WAF openers.", TODAY, {"status", "window"})
        self.assertEqual(problems, [])
        self.assertEqual(len(removed), 2)
        self.assertIsNone(parsed.vessels.commercial_status)
        self.assertIsNone(parsed.vessels.open_end_from)
        self.assertIsNone(parsed.vessels.updated_from)
        self.assertEqual(parsed.vessels.parent_zone, ["West Africa"])

    def test_refusals_are_recognised_in_the_conversation(self):
        messages = [HumanMessage("Show me the WAF openers."),
                    ToolMessage(content=f"That search failed: {tools.STATUS_REFUSAL}", tool_call_id="s1", status="error")]
        self.assertEqual(tools._refused_before(messages), {"status"})
        self.assertEqual(tools._refused_before([*messages, HumanMessage("next question")]), set())

    def test_other_windows_are_never_touched(self):
        self.assertEqual(unasked("Which vessels are fixed?", vessels={"commercial_status": "FIXED", "open_end_from": TODAY}), [])

    def test_form_answers_count_as_the_traders_words(self):
        messages = [HumanMessage("Show me the C3 iron ore orders."),
                    *form_exchange("Which status?", "Status", "Open only", "c1")]
        words = tools._trader_words(messages)
        self.assertIn("Open only", words)
        self.assertNotIn("Which status", words)


class RepeatedQuestionTests(unittest.TestCase):
    def test_a_question_naming_an_earlier_answer_is_a_repeat(self):
        answered = [("Destination", "What do you mean by North China?", "North China")]
        question = {"question": "Which specific North China port do you want?", "header": "Port"}
        self.assertEqual(tools._earlier_answer(question, answered), "North China")

    def test_the_same_header_is_a_repeat(self):
        answered = [("Zone", "Which zone does ECSA refer to?", "East Coast South America")]
        question = {"question": "Please confirm the region", "header": "zone"}
        self.assertEqual(tools._earlier_answer(question, answered), "East Coast South America")

    def test_a_new_question_is_not(self):
        answered = [("Window", "Which period?", "Updated in the last 7 days")]
        question = {"question": "Which load zone?", "header": "Zone"}
        self.assertIsNone(tools._earlier_answer(question, answered))

    def test_only_answers_since_the_latest_question_count(self):
        messages = [HumanMessage("first question"), *form_exchange("Which zone?", "Zone", "West Africa", "a1"),
                    HumanMessage("second question")]
        self.assertEqual(tools._answered_questions(messages), [])

    def test_a_repeat_is_answered_without_showing_the_form(self):
        messages = [HumanMessage("Show me orders from PDM to North China."),
                    *form_exchange("What do you mean by North China?", "Destination", "North China", "a1")]
        runtime = SimpleNamespace(state={"messages": messages})
        question = tables_question("Which specific port in North China?", "Port")
        result = tools.ask_user.func(questions=[question], runtime=runtime)
        self.assertEqual(result["Which specific port in North China?"], "North China")
        self.assertIn(tools.ALREADY_ANSWERED_KEY, result)


def tables_question(text, header):
    return tools.Question(question=text, header=header, options=[tools.Option(label="Tianjin", description="a port")])


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
