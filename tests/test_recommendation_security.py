import json
import os
import unittest
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import jwt
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from test_recommendations import ORDER, VESSEL

from ai_platform.app.api import recommendations as api
from ai_platform.recommendations import pg_store


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(api.router)
        self.client = TestClient(app)
        self.env = patch.dict(
            os.environ,
            {"CHAINLIT_AUTH_SECRET": "unit-test-signing-key-not-a-real-secret"},
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def token(self, expiry=60):
        return jwt.encode(
            {
                "identifier": "Alice",
                "metadata": {},
                "exp": datetime.now(UTC) + timedelta(seconds=expiry),
            },
            os.environ["CHAINLIT_AUTH_SECRET"],
            algorithm="HS256",
        )

    def test_requires_valid_session(self):
        for token in (None, "invalid", self.token(-60)):
            headers = {"Authorization": "Bearer " + token} if token else {}
            self.assertEqual(
                self.client.get(
                    "/api/recommendations/session", headers=headers
                ).status_code,
                401,
            )
        response = self.client.get(
            "/api/recommendations/session",
            headers={"Authorization": "Bearer " + self.token()},
        )
        self.assertEqual(response.json()["identifier"], "Alice")

    def test_no_client_identity_and_no_cross_site_writes(self):
        body = {
            "action": "reject",
            "reason": "Review complete",
            "expected_version": 0,
            "request_id": str(uuid4()),
        }
        headers = {"Authorization": "Bearer " + self.token(), "X-OI-Request": "1"}
        url = "/api/recommendations/" + str(uuid4()) + "/decisions"
        self.assertEqual(
            self.client.post(
                url,
                json=body,
                headers={**headers, "Origin": "https://untrusted.example"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                url, json=body, headers={"Authorization": headers["Authorization"]}
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                url, json={**body, "entered_by": "Mallory"}, headers=headers
            ).status_code,
            422,
        )
        with patch.object(
            api.store, "decide", AsyncMock(return_value={"version": 1})
        ) as save:
            self.assertEqual(
                self.client.post(url, json=body, headers=headers).status_code, 201
            )
            self.assertEqual(save.call_args.args[2], "Alice")


class FreshnessTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2025, 9, 27, 12, tzinfo=UTC)
        self.payload = {
            "as_of": "2025-09-27",
            "order": ORDER,
            "candidates": [{"vessel_id": "A", "evidence": VESSEL}],
            "rejected": [],
        }

    def check(self, **changes):
        args = {
            "payload": self.payload,
            "created_at": self.now,
            "order": ORDER,
            "vessel": VESSEL,
            "selected_id": "A",
            "now": self.now,
            "as_of": date(2025, 9, 27),
        }
        pg_store.check_fresh(**{**args, **changes})

    def test_unchanged_evidence_allowed(self):
        self.check()

    def test_expired_changed_or_missing_evidence_rejected(self):
        for changes in (
            {"created_at": self.now - timedelta(minutes=31)},
            {"as_of": date(2025, 9, 28)},
            {"order": {**ORDER, "cargo_weight_min": 200}},
            {"vessel": {**VESSEL, "commercial_status": "FIXED"}},
            {"vessel": None},
        ):
            with (
                self.subTest(changes=changes),
                self.assertRaises(HTTPException) as error,
            ):
                self.check(**changes)
            self.assertEqual(error.exception.status_code, 409)


class LedgerTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_revision_conflict_prevents_insert(self):
        connection = MagicMock()
        connection.execute = AsyncMock()
        connection.fetchrow = AsyncMock(
            side_effect=[
                {"payload": json.dumps({}), "created_at": datetime.now(UTC)},
                None,
            ]
        )
        connection.fetchval = AsyncMock(return_value=2)
        transaction = MagicMock()
        transaction.__aenter__ = AsyncMock()
        transaction.__aexit__ = AsyncMock(return_value=False)
        connection.transaction.return_value = transaction
        acquired = MagicMock()
        acquired.__aenter__ = AsyncMock(return_value=connection)
        acquired.__aexit__ = AsyncMock(return_value=False)
        pool = MagicMock()
        pool.acquire.return_value = acquired
        body = api.Decision(
            action="reject", reason="Reason", expected_version=1, request_id=uuid4()
        )
        with (
            patch.object(pg_store, "_get_pool", AsyncMock(return_value=pool)),
            self.assertRaises(HTTPException) as error,
        ):
            await pg_store.decide(uuid4(), body, "Alice")
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(
            connection.execute.await_count, 1
        )  # timeout setup only, no insert
