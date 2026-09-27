import asyncio
import json
import unittest
from contextlib import asynccontextmanager
from datetime import date
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_recommendations import ORDER, VESSEL

from ai_platform.app.api import recommendations as api
from ai_platform.recommendations.orchestration import run_workflow
from ai_platform.recommendations.run_evaluation import run_cases


def response(evidence, hold=False):
    return {
        "content": json.dumps(
            {
                "assessments": [
                    {
                        "vessel_id": vessel["vessel_id"],
                        "verdict": "hold" if hold else "consider",
                        "rationale": "Reported capacity requires broker confirmation.",
                        "evidence_keys": [vessel["evidence_prefix"] + "dwt"],
                    }
                    for vessel in reversed(evidence["vessels"])
                ]
            }
        ),
        "finish_reason": "stop",
        "model": "test-model",
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    }


class OrchestrationTests(unittest.IsolatedAsyncioTestCase):
    async def run_case(self, reviewer, vessels=None, **kwargs):
        return await run_workflow(
            ORDER, vessels or [VESSEL], date(2025, 9, 27), reviewer=reviewer, **kwargs
        )

    async def test_parallel_reviews_and_veto(self):
        roles = set()
        both_started = asyncio.Event()

        async def reviewer(role, evidence, model):
            roles.add(role)
            if len(roles) == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), 1)
            return response(evidence, hold=role == "commercial_risk")

        run = await self.run_case(reviewer)
        self.assertEqual(run["review_status"], "completed")
        self.assertEqual(run["candidates"], [])
        self.assertEqual(run["held"][0]["vessel_id"], "A")
        self.assertEqual(run["reviews"][0]["usage"]["prompt_tokens"], 100)

    async def test_rank_order_and_excluded_vessels_never_sent(self):
        async def reviewer(role, evidence, model):
            self.assertNotIn("FIXED", json.dumps(evidence))
            return response(evidence)

        run = await self.run_case(
            reviewer,
            [
                VESSEL,
                {**VESSEL, "vessel_id": "B"},
                {**VESSEL, "vessel_id": "C", "commercial_status": "FIXED"},
            ],
        )
        self.assertEqual([v["vessel_id"] for v in run["candidates"]], ["B", "A"])
        self.assertEqual(run["rejected"][0]["vessel_id"], "C")

    async def test_invalid_model_outputs_are_held(self):
        for mutation in (
            "invented_vessel",
            "invented_evidence",
            "missing_vessel",
            "duplicate",
            "truncated",
            "refused",
            "malformed",
        ):

            async def reviewer(role, evidence, model, mutation=mutation):
                output = response(evidence)
                body = json.loads(output["content"])
                item = body["assessments"][0]
                if mutation == "invented_vessel":
                    item["vessel_id"] = "ghost"
                elif mutation == "invented_evidence":
                    item["evidence_keys"] = ["vessels.99.profit"]
                elif mutation == "missing_vessel":
                    body["assessments"] = []
                elif mutation == "duplicate":
                    body["assessments"].append(item)
                elif mutation == "truncated":
                    output["finish_reason"] = "length"
                elif mutation == "refused":
                    output["refusal"] = "refused"
                output["content"] = (
                    "not-json" if mutation == "malformed" else json.dumps(body)
                )
                return output

            with self.subTest(mutation=mutation):
                run = await self.run_case(reviewer)
                self.assertEqual(run["review_status"], "degraded")
                self.assertFalse(run["candidates"])
                self.assertEqual(run["reviews"][0]["error"], "invalid_response")

    async def test_timeout_and_provider_error_are_safe(self):
        async def slow(role, evidence, model):
            await asyncio.sleep(1)

        run = await self.run_case(slow, timeout=0.001)
        self.assertEqual(run["reviews"][0]["error"], "timeout")

        async def failure(role, evidence, model):
            raise RuntimeError("secret-provider-key")

        run = await self.run_case(failure)
        self.assertNotIn("secret-provider-key", json.dumps(run))
        self.assertEqual(run["review_status"], "degraded")

    async def test_rules_only_and_empty_shortlist_skip_provider(self):
        reviewer = AsyncMock()
        run = await self.run_case(reviewer, mode="rules")
        self.assertEqual(run["review_status"], "rules_only")
        run = await self.run_case(reviewer, [{**VESSEL, "dwt": 1}])
        self.assertEqual(run["review_status"], "not_needed")
        reviewer.assert_not_called()

    async def test_evaluation_does_not_send_labels_and_records_usage(self):
        async def reviewer(role, evidence, model):
            self.assertNotIn("relevant", evidence)
            self.assertNotIn("forbidden", evidence)
            return response(evidence)

        result = await run_cases(
            [
                {
                    "id": "one",
                    "as_of": "2025-09-27",
                    "order": ORDER,
                    "vessels": [VESSEL],
                    "relevant": ["A"],
                    "forbidden": [],
                }
            ],
            mode="ai",
            reviewer=reviewer,
        )
        self.assertEqual(result["mean_precision"], 1)
        self.assertEqual(result["predictions"][0]["prompt_tokens"], 200)
        self.assertIsNone(result["predictions"][0]["cost_usd"])


class RecommendationApiTests(unittest.TestCase):
    def setUp(self):
        @asynccontextmanager
        async def slot(trader):
            yield {}

        self.slot_patch = patch.object(api.operations, "workflow_slot", slot)
        self.slot_patch.start()
        self.addCleanup(self.slot_patch.stop)
        app = FastAPI()
        app.include_router(api.router)
        app.dependency_overrides[api.current_trader] = lambda: "Verified trader"
        self.client = TestClient(app)

    def test_generate_uses_authenticated_identity(self):
        async def reviewer(role, evidence, model):
            return response(evidence, hold=True)

        with (
            patch.object(api, "fetch_rows", AsyncMock(side_effect=[[ORDER], [VESSEL]])),
            patch("ai_platform.recommendations.orchestration.model_review", reviewer),
            patch.object(api, "working_date", return_value=date(2025, 9, 27)),
            patch.object(
                api.store, "save", AsyncMock(return_value={"id": str(uuid4())})
            ) as save,
        ):
            result = self.client.post("/api/recommendations", json={"order_id": "123"})
        self.assertEqual(result.status_code, 201)
        self.assertEqual(save.call_args.args[1], "Verified trader")
        self.assertEqual(save.call_args.args[0]["held"][0]["vessel_id"], "A")
