import asyncio
import os
import tempfile
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

from ai_platform.app.api.recommendations import Decision
from ai_platform.recommendations import store
from ai_platform.recommendations.engine import recommend

ORDER = {
    "order_id": "123",
    "cargo_weight_min": 100,
    "laycan_start": "2025-09-27",
    "laycan_end": "2025-09-30",
    "load_zone": "ECSA",
}
VESSEL = {
    "vessel_id": "A",
    "dwt": 120,
    "open_date_start": "2025-09-27",
    "open_date_end": "2025-09-30",
    "parent_zone": "ECSA",
    "commercial_status": "AVAILABLE",
    "update_date": "2025-09-27",
}


class RecommendationTests(unittest.TestCase):
    def run_case(self, vessels, order=None):
        return asyncio.run(recommend(order or ORDER, vessels, date(2025, 9, 27)))

    def test_rejects_unsafe_and_missing_evidence(self):
        for change in [
            {"dwt": 90},
            {"dwt": None},
            {"commercial_status": "FIXED"},
            {"commercial_status": "ON SUBS"},
            {"update_date": "2025-09-24"},
            {"update_date": "2025-09-28"},
            {"open_date_start": "2025-10-01"},
            {"open_date_end": None},
        ]:
            with self.subTest(change=change):
                result = self.run_case([{**VESSEL, **change}])
                self.assertEqual(result["candidates"], [])
                self.assertEqual(len(result["rejected"]), 1)

    def test_region_ranking_and_synthetic_assignment_ignored(self):
        result = self.run_case(
            [
                {**VESSEL, "vessel_id": "B", "parent_zone": "OTHER", "order_id": "123"},
                VESSEL,
            ]
        )
        self.assertEqual([v["vessel_id"] for v in result["candidates"]], ["A", "B"])

    def test_store_validates_decisions_and_preserves_history(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {"OI_RECOMMENDATION_DB": directory + "/ledger.db"}),
        ):
            run = store.save(
                self.run_case([VESSEL, {**VESSEL, "vessel_id": "B", "dwt": 80}])
            )

            def decision(action, vessel):
                return SimpleNamespace(
                    action=action,
                    vessel_id=vessel,
                    entered_by="Trader",
                    reason="Broker confirmation",
                )

            with self.assertRaises(ValueError):
                store.decide(run["id"], decision("accept", "B"))
            with self.assertRaises(ValueError):
                store.decide(run["id"], decision("override", "unknown"))
            store.decide(run["id"], decision("accept", "A"))
            history = store.decide(run["id"], decision("override", "B"))
            self.assertEqual([row["action"] for row in history], ["override", "accept"])
            self.assertEqual(store.get(run["id"])["candidates"][0]["vessel_id"], "A")

    def test_client_supplied_identity_rejected(self):
        with self.assertRaises(ValidationError):
            Decision(action="reject", entered_by="  ", reason="Reason")


class EvaluationTests(unittest.TestCase):
    def test_metrics_and_abstention(self):
        from ai_platform.recommendations.evaluate import evaluate

        result = evaluate(
            [
                {"id": "1", "relevant": ["A"], "forbidden": ["B"]},
                {"id": "2", "relevant": [], "forbidden": ["B"]},
            ],
            [{"id": "1", "vessels": ["B", "A"]}, {"id": "2", "vessels": []}],
        )
        self.assertEqual(result["unsafe_case_rate"], 0.5)
        self.assertEqual(result["mean_precision"], 0.75)
        self.assertEqual(result["positive_case_count"], 1)
        self.assertEqual(result["mean_recall_on_positive_cases"], 1)
        self.assertEqual(result["abstention_accuracy"], 1)
        self.assertTrue(result["cases"][1]["correct_abstention"])

    def test_missing_predictions_fail(self):
        from ai_platform.recommendations.evaluate import evaluate

        with self.assertRaises(ValueError):
            evaluate([{"id": "1", "relevant": [], "forbidden": []}], [])
