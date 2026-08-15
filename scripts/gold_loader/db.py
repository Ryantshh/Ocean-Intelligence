"""Direct Postgres access to the gold-layer tables, via SUPABASE_DB_URL.

Deliberately independent of ai_platform/backend/db.py, which reads
CHAINLIT_DATABASE_URL (asyncpg, shared with Chainlit's own tables) and only
ever reads public.orders/public.tonnage. This loader writes
public.order_test/public.tonnage_test and uses SUPABASE_DB_URL, its own
already-provisioned env var.

pg8000 (pure Python) is used instead of asyncpg so the Lambda deployment
package doesn't need a platform-matched compiled wheel -- this loader's
invocations are batchy (once per Glue run), not latency sensitive like
ai_platform's FastAPI reads, so pg8000's synchronous, single-connection-per-
call model is a fine trade for simpler packaging.
"""

from typing import Any
from urllib.parse import urlparse

import pg8000.dbapi

BATCH_SIZE = 500


def connect(dsn: str):
    parsed = urlparse(dsn.replace("postgresql+asyncpg://", "postgresql://"))
    return pg8000.dbapi.Connection(
        host=parsed.hostname,
        port=parsed.port or 5432,
        user=parsed.username,
        password=parsed.password,
        database=parsed.path.lstrip("/"),
        ssl_context=True,
    )


def fetch_existing(
    connection, table: str, pk_column: str, embedding_columns: tuple[str, ...]
) -> dict[Any, dict[str, Any]]:
    """Return {pk_value: {"embedding_source_hash": ..., "<col>_embedding": ...}}.

    Lets the caller skip re-embedding (and re-calling Cohere for) rows whose
    embeddable fields haven't changed since the last run.
    """
    select_columns = [pk_column, "embedding_source_hash", *embedding_columns]
    cursor = connection.cursor()
    cursor.execute(f"SELECT {', '.join(select_columns)} FROM public.{table}")
    existing = {}
    for record in cursor.fetchall():
        row = dict(zip(select_columns, record))
        existing[row[pk_column]] = row
    cursor.close()
    return existing


def _vector_literal(value):
    """pgvector text format. Accepts either a fresh list[float] (from a new
    Cohere call) or a value already in text form (as reused rows carry
    forward from fetch_existing(), whatever shape pg8000 hands back for an
    unrecognized column OID)."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return "[" + ",".join(repr(float(component)) for component in value) + "]"


def upsert(
    connection,
    table: str,
    rows: list[dict[str, Any]],
    pk_column: str,
    columns: tuple[str, ...],
    embedding_columns: tuple[str, ...],
) -> None:
    if not rows:
        return

    all_columns = [*columns, *embedding_columns]
    column_list = ", ".join(all_columns)
    placeholder_list = ", ".join(["%s"] * len(columns) + ["%s::vector"] * len(embedding_columns))
    assignments = ", ".join(f"{col} = EXCLUDED.{col}" for col in all_columns if col != pk_column)
    sql = (
        f"INSERT INTO public.{table} ({column_list}) VALUES ({placeholder_list}) "
        f"ON CONFLICT ({pk_column}) DO UPDATE SET {assignments}"
    )

    cursor = connection.cursor()
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start : start + BATCH_SIZE]
        params = [
            [row.get(col) for col in columns] + [_vector_literal(row.get(col)) for col in embedding_columns]
            for row in batch
        ]
        cursor.executemany(sql, params)
    connection.commit()
    cursor.close()
