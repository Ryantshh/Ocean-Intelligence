"""Opt-in integration test. All ledger writes are rolled back.

OI_RUN_DB_TESTS=1 python -m unittest discover -s tests -p test_recommendation_postgres.py -v
"""

import os
import unittest
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import asyncpg
from fastapi import HTTPException

from ai_platform.app import accounts
from ai_platform.app.api.recommendations import Decision, EvaluationLabels
from ai_platform.backend.clock import working_date
from ai_platform.backend.db import _get_pool, close_pool
from ai_platform.recommendations import labels, operations, pg_store
from ai_platform.recommendations.source import VESSELS_SQL


@unittest.skipUnless(
    os.environ.get("OI_RUN_DB_TESTS") == "1", "requires configured database"
)
class PostgresLedgerTests(unittest.IsolatedAsyncioTestCase):
    async def test_persistence_idempotency_conflict_freshness_and_immutability(self):
        pool = await _get_pool()
        try:
            async with pool.acquire() as connection:
                transaction = connection.transaction()
                await transaction.start()
                try:

                    @asynccontextmanager
                    async def acquire(**kwargs):
                        yield connection

                    class TestPool:
                        pass

                    test_pool = TestPool()
                    test_pool.acquire = acquire
                    with patch.object(
                        pg_store, "_get_pool", AsyncMock(return_value=test_pool)
                    ):
                        as_of = working_date()
                        order = pg_store.plain(
                            await connection.fetchrow(
                                "SELECT order_id::text AS order_id, load_port, load_zone, cargo_weight_min, laycan_start, laycan_end FROM public.order_test WHERE update_date::date <= $1 LIMIT 1",
                                as_of,
                            )
                        )
                        vessels = await connection.fetch(VESSELS_SQL, as_of)
                        self.assertIsNotNone(order)
                        self.assertTrue(vessels)
                        vessel = pg_store.plain(vessels[0])
                        payload = {
                            "as_of": as_of.isoformat(),
                            "order": order,
                            "candidates": [
                                {"vessel_id": vessel["vessel_id"], "evidence": vessel}
                            ],
                            "rejected": [],
                        }
                        run = await pg_store.save(payload, "integration-test")
                        identifier = UUID(run["id"])
                        body = Decision(
                            action="accept",
                            vessel_id=vessel["vessel_id"],
                            reason="Rolled back integration test",
                            expected_version=0,
                            request_id=uuid4(),
                        )
                        saved = await pg_store.decide(
                            identifier, body, "integration-test"
                        )
                        self.assertEqual(saved["version"], 1)
                        self.assertEqual(
                            saved["decisions"][0]["entered_by"], "integration-test"
                        )
                        repeated = await pg_store.decide(
                            identifier, body, "integration-test"
                        )
                        self.assertEqual(len(repeated["decisions"]), 1)
                        stale = body.model_copy(update={"request_id": uuid4()})
                        with self.assertRaises(HTTPException) as conflict:
                            await pg_store.decide(identifier, stale, "integration-test")
                        self.assertEqual(conflict.exception.status_code, 409)
                        changed = {
                            **payload,
                            "order": {**order, "load_port": "different evidence"},
                        }
                        changed_run = await pg_store.save(changed, "integration-test")
                        with self.assertRaises(HTTPException) as outdated:
                            await pg_store.decide(
                                UUID(changed_run["id"]), stale, "integration-test"
                            )
                        self.assertEqual(outdated.exception.status_code, 409)
                        with self.assertRaises(asyncpg.RaiseError):
                            async with connection.transaction():
                                await connection.execute(
                                    "UPDATE public.oi_recommendations SET created_at=$1 WHERE id=$2",
                                    datetime.now(UTC),
                                    identifier,
                                )
                        self.assertEqual((await pg_store.get(identifier))["version"], 1)
                        with (
                            patch.object(
                                accounts, "_get_pool", AsyncMock(return_value=test_pool)
                            ),
                            patch.object(
                                labels, "_get_pool", AsyncMock(return_value=test_pool)
                            ),
                            patch.object(
                                operations,
                                "_get_pool",
                                AsyncMock(return_value=test_pool),
                            ),
                            patch.dict(os.environ, {"OI_AUTH_MODE": "accounts"}),
                        ):
                            actor = "integration-" + uuid4().hex
                            await accounts.manage(
                                "add",
                                actor,
                                accounts.hash_password("test-only-password-483"),
                            )
                            metadata = await accounts.authenticate(
                                actor, "test-only-password-483"
                            )
                            self.assertIsNotNone(metadata)
                            await accounts.verify_session(actor, metadata)
                            await accounts.manage("disable", actor)
                            with self.assertRaises(HTTPException):
                                await accounts.verify_session(actor, metadata)
                            for _ in range(operations.PER_TRADER_PER_MINUTE):
                                request = await operations.admit(actor)
                                await operations.finish(
                                    request,
                                    "completed",
                                    12,
                                    {"prompt_tokens": 2, "completion_tokens": 1},
                                )
                            with self.assertRaises(HTTPException) as limited:
                                await operations.admit(actor)
                            self.assertEqual(limited.exception.status_code, 429)
                            label = await labels.save(
                                identifier,
                                EvaluationLabels(
                                    relevant=[vessel["vessel_id"]],
                                    forbidden=[],
                                    rationale="Synthetic integration label; transaction rolled back",
                                    complete_review=True,
                                ),
                                actor,
                            )
                            exported = await labels.export_cases()
                            self.assertTrue(
                                any(
                                    case["label_id"] == label["id"] for case in exported
                                )
                            )
                            with self.assertRaises(asyncpg.RaiseError):
                                async with connection.transaction():
                                    await connection.execute(
                                        "DELETE FROM public.oi_evaluation_labels WHERE id=$1",
                                        UUID(label["id"]),
                                    )
                            self.assertGreaterEqual(
                                (await operations.metrics())["completed"], 6
                            )

                finally:
                    await transaction.rollback()
        finally:
            await close_pool()
