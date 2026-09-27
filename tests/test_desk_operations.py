import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException

from ai_platform.app import accounts
from ai_platform.recommendations import labels, operations
from ai_platform.recommendations.benchmark import benchmark


class PasswordTests(unittest.TestCase):
    def test_salted_hashes_and_bad_passwords(self):
        password = "testing-long-password"
        first = accounts.hash_password(password)
        second = accounts.hash_password(password)
        self.assertNotEqual(first, second)
        self.assertTrue(accounts.verify_password(password, first))
        self.assertFalse(accounts.verify_password("incorrect-password", first))
        self.assertFalse(accounts.verify_password(password, "broken"))
        self.assertFalse(accounts.verify_password("a" * 1025, first))
        with self.assertRaises(ValueError):
            accounts.hash_password("short")

    def test_production_rejects_development_login(self):
        with (
            patch.dict(
                os.environ,
                {"OI_ENVIRONMENT": "production", "OI_AUTH_MODE": "development"},
            ),
            self.assertRaises(RuntimeError),
        ):
            accounts.auth_mode()
        with patch.dict(
            os.environ, {"OI_ENVIRONMENT": "production", "OI_AUTH_MODE": "accounts"}
        ):
            self.assertEqual(accounts.auth_mode(), "accounts")


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_account_mode_rejects_legacy_session_before_database(self):
        with (
            patch.dict(os.environ, {"OI_AUTH_MODE": "accounts"}),
            patch.object(accounts, "_get_pool", AsyncMock()) as pool,
            self.assertRaises(HTTPException),
        ):
            await accounts.verify_session("dev", {"role": "dev"})
        pool.assert_not_called()

    async def test_unicode_development_password_does_not_crash(self):
        with patch.dict(
            os.environ, {"OI_ENVIRONMENT": "development", "OI_AUTH_MODE": "development"}
        ):
            self.assertIsNone(await accounts.authenticate("dev", "文字password"))


class LabelTests(unittest.TestCase):
    def test_unknown_duplicate_and_conflicting_ids_rejected(self):
        payload = {"candidates": [{"vessel_id": "A"}], "rejected": [{"vessel_id": "B"}]}
        labels.validate_labels(payload, ["A"], ["B"])
        for good, bad in [(["A"], ["A"]), (["unknown"], []), (["A", "A"], [])]:
            with self.subTest(good=good, bad=bad), self.assertRaises(HTTPException):
                labels.validate_labels(payload, good, bad)


class RequestLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_busy_queue_rejects_before_inserting(self):
        connection = MagicMock()
        connection.execute = AsyncMock()
        connection.fetchrow = AsyncMock(return_value={"recent": 0, "active": 4})
        transaction = MagicMock()
        transaction.__aenter__ = AsyncMock()
        transaction.__aexit__ = AsyncMock(return_value=False)
        connection.transaction.return_value = transaction
        acquired = MagicMock()
        acquired.__aenter__ = AsyncMock(return_value=connection)
        acquired.__aexit__ = AsyncMock(return_value=False)
        pool = MagicMock()
        pool.acquire.return_value = acquired
        with (
            patch.object(operations, "_get_pool", AsyncMock(return_value=pool)),
            self.assertRaises(HTTPException) as error,
        ):
            await operations.admit("Trader")
        self.assertEqual(error.exception.status_code, 429)
        self.assertEqual(error.exception.headers["Retry-After"], "60")
        self.assertEqual(connection.execute.await_count, 2)  # setup and lock, no insert

    async def test_finish_on_failure_and_timeout(self):
        with (
            patch.object(operations, "admit", AsyncMock(return_value=uuid4())),
            patch.object(operations, "finish", AsyncMock()) as finish,
        ):
            with self.assertRaises(ValueError):
                async with operations.workflow_slot("Trader"):
                    raise ValueError("failed")
            self.assertEqual(finish.call_args.args[1], "failed")
            with (
                patch.object(operations, "REQUEST_TIMEOUT_SECONDS", 0.001),
                self.assertRaises(HTTPException) as error,
            ):
                async with operations.workflow_slot("Trader"):
                    await asyncio.sleep(1)
            self.assertEqual(error.exception.status_code, 504)
            self.assertEqual(finish.call_args.args[1], "timeout")


class BenchmarkTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_rank_disagreement_reported(self):
        count = 0

        async def runner(cases, mode, model=None):
            nonlocal count
            count += 1
            return {
                "predictions": [
                    {
                        "id": "one",
                        "vessels": ["A"] if count % 2 else ["B"],
                        "review_status": "completed",
                    }
                ],
                "mean_recall_on_positive_cases": 0.5,
                "unsafe_case_rate": 0,
                "degraded_cases": 0,
                "mean_latency_ms": 10,
            }

        report = await benchmark(
            [{"id": "one"}], models=["test-model"], repeats=2, runner=runner
        )
        self.assertEqual(count, 3)
        self.assertEqual(report["summary"][1]["ranking_agreement"], 0)
        self.assertEqual(report["summary"][1]["comparable_cases"], 1)
        self.assertIsNone(report["summary"][0]["ranking_agreement"])
