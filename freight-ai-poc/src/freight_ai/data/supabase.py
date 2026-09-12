"""Read-only Supabase snapshots. No model-generated SQL or database writes."""

import asyncio
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

from freight_ai.data.models import Order, Provenance, Tonnage

ORDER_FIELDS = "order_id,date_received,update_date,laycan_start,laycan_end,load_port,discharge_port,cargo_type,cargo_description,load_zone,discharge_parent_zone,cargo_weight_min,cargo_weight_max"
VESSEL_FIELDS = "tonnage_row_key,vessel_id,update_date,parent_zone,vessel_status,dwt,commercial_status,ship_type,ship_size,ballast_laden,destination,open_area,eta,open_date_start,open_date_end,first_date_received"


def canonical(kind, row, index):
    values = dict(row)
    fingerprint = hashlib.sha256(
        json.dumps(values, default=str, sort_keys=True).encode()
    ).hexdigest()
    if kind == "orders":
        key = str(values.pop("order_id"))
        values["discharge_zone"] = values.pop("discharge_parent_zone")
        cls = Order
    else:
        key = str(values.pop("tonnage_row_key"))
        values["vessel_name"] = values.pop(
            "vessel_id"
        )  # source identifier; not a verified vessel name
        values["eta_date_start"] = values.pop("eta")
        values["date_received"] = values["first_date_received"]
        cls = Tonnage
    for field in (
        "laycan_start",
        "laycan_end",
        "open_date_start",
        "open_date_end",
        "eta_date_start",
    ):
        if isinstance(values.get(field), datetime):
            values[field] = values[field].date()
    return cls(
        record_id=f"supabase:{kind}:{key}",
        source=Provenance(
            file="Supabase",
            sheet="public.order_test" if kind == "orders" else "public.tonnage_test",
            row=index,
            sha256=fingerprint,
        ),
        **values,
    )


async def _read(dsn):
    import asyncpg

    connection = await asyncpg.connect(
        dsn,
        timeout=30,
        ssl="require",
        server_settings={"default_transaction_read_only": "on"},
    )
    try:
        async with connection.transaction(isolation="repeatable_read", readonly=True):
            result = {}
            for kind, table, fields in [
                ("orders", "order_test", ORDER_FIELDS),
                ("tonnage", "tonnage_test", VESSEL_FIELDS),
            ]:
                rows = await connection.fetch(
                    f"SELECT {fields} FROM public.{table} ORDER BY 1 LIMIT 100001",
                    timeout=30,
                )
                if len(rows) > 100000:
                    raise ValueError(
                        "Snapshot exceeds 100,000 rows; refusing partial results."
                    )
                try:
                    result[kind] = [
                        canonical(kind, row, i) for i, row in enumerate(rows, 1)
                    ]
                except ValueError:
                    raise ValueError(
                        f"Supabase {table} contains records incompatible with the canonical schema; no partial snapshot loaded."
                    ) from None
            return result
    finally:
        await connection.close()


def connection_dsn():
    from dotenv import dotenv_values

    env = dotenv_values(Path(__file__).resolve().parents[4] / ".env")
    dsn = (
        os.environ.get("POC_SUPABASE_DB_URL")
        or env.get("POC_SUPABASE_DB_URL")
        or os.environ.get("CHAINLIT_DATABASE_URL")
        or env.get("CHAINLIT_DATABASE_URL")
    )
    if not dsn:
        raise ValueError(
            "Configure POC_SUPABASE_DB_URL or the main project CHAINLIT_DATABASE_URL."
        )
    return dsn.replace("postgresql+asyncpg://", "postgresql://")


def load_snapshot():
    return run_read(_read(connection_dsn()))


def run_read(coroutine):
    try:
        return asyncio.run(coroutine)
    except Exception:
        raise ValueError("Could not read a complete valid Supabase result. Check connectivity, credentials and source validation. No database changes were attempted.") from None


async def _search(dsn, intent, as_of):
    import asyncpg
    from freight_ai.data.sql_search import compile_search
    sql, params = compile_search(intent, as_of)
    connection = await asyncpg.connect(dsn, timeout=30, ssl="require",
        server_settings={"default_transaction_read_only": "on"})
    try:
        async with connection.transaction(isolation="repeatable_read", readonly=True):
            rows = await connection.fetch(sql, *params, timeout=30)
            if len(rows) > 100000:
                raise ValueError("Candidate limit exceeded; refusing partial results")
            return {intent.dataset: [canonical(intent.dataset,row,i) for i,row in enumerate(rows,1)]}
    finally:
        await connection.close()


def search_records(intent, as_of):
    return run_read(_search(connection_dsn(), intent, as_of))
