# Database timestamps intentionally have no timezone.
# ruff: noqa: DTZ001
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from ai_platform.trader_override.trader_override import OverrideRequest, submit_override


class OverrideTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_audit_failure_exits_transaction_with_error(self):
        row = {
            "commercial_status": "AVAILABLE",
            "open_area": "ECSA",
            "open_date_start": datetime(2025, 9, 27),
            "open_date_end": datetime(2025, 9, 30),
            "order_id": "123",
        }
        connection = MagicMock()
        connection.fetchrow = AsyncMock(
            side_effect=[row, row, RuntimeError("audit unavailable")]
        )
        connection.execute = AsyncMock()
        transaction = MagicMock()
        transaction.__aenter__ = AsyncMock()
        transaction.__aexit__ = AsyncMock(return_value=False)
        connection.transaction.return_value = transaction
        acquired = MagicMock()
        acquired.__aenter__ = AsyncMock(return_value=connection)
        acquired.__aexit__ = AsyncMock(return_value=False)
        pool = MagicMock()
        pool.acquire.return_value = acquired
        with patch("ai_platform.backend.db._get_pool", AsyncMock(return_value=pool)):  # noqa: SIM117
            with self.assertRaises(HTTPException) as raised:
                await submit_override(
                    OverrideRequest(
                        vessel_id="A",
                        base_tonnage_row_key="key",
                        override_status="AVAILABLE",
                    ),
                    trader="Verified trader",
                )
        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(
            connection.fetchrow.call_args_list[2].args[-1], "Verified trader"
        )
        self.assertIs(transaction.__aexit__.call_args.args[0], RuntimeError)
        self.assertIn("FOR UPDATE", connection.fetchrow.call_args_list[0].args[0])
