# ruff: noqa: C408, DTZ001
# Source timestamps are intentionally timezone-naive.
from datetime import datetime

from freight_ai.data.supabase import _read, canonical
from freight_ai.service import current_records


def test_mapping_preserves_missing_status_and_source_identity():
    row = dict(
        tonnage_row_key="report-1",
        vessel_id="ship-7",
        eta=None,
        first_date_received=datetime(2026, 1, 1),
        commercial_status=None,
    )
    record = canonical("tonnage", row, 1)
    assert record.record_id == "supabase:tonnage:report-1"
    assert record.vessel_name == "ship-7"
    assert record.commercial_status is None
    assert record.date_received == row["first_date_received"]
    assert row["vessel_id"] == "ship-7"
    assert current_records(
        {"data_source": "supabase", "_snapshot": {"tonnage": [record]}}
    )["tonnage"] == [record]


def test_connection_enforces_readonly_and_only_selects(monkeypatch):
    import asyncio

    import asyncpg

    calls = []

    class Transaction:
        async def __aenter__(self):
            pass

        async def __aexit__(self, *args):
            pass

    class Connection:
        def transaction(self, **kwargs):
            assert kwargs == dict(isolation="repeatable_read", readonly=True)
            return Transaction()

        async def fetch(self, sql, **kwargs):
            assert sql.startswith("SELECT ")
            assert "embedding" not in sql
            calls.append(sql)
            return []

        async def close(self):
            calls.append("closed")

    async def connect(*args, **kwargs):
        assert kwargs["server_settings"]["default_transaction_read_only"] == "on"
        assert kwargs["ssl"] == "require"
        return Connection()

    monkeypatch.setattr(asyncpg, "connect", connect)
    assert asyncio.run(_read("unused")) == {"orders": [], "tonnage": []}
    assert len(calls) == 3 and calls[-1] == "closed"
