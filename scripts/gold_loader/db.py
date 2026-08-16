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

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

import pg8000.dbapi

from .logging_utils import get_logger

logger = get_logger("gold_loader")

BATCH_SIZE = 500

# pgvector stores each vector(N) dimension as a 4-byte float4 on disk.
BYTES_PER_EMBEDDING_DIMENSION = 4


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


def fetch_existing(connection, table: str, pk_column: str, columns: tuple[str, ...]) -> dict[Any, dict[str, Any]]:
    """Return {pk_value: {column: value, ...}} for every column in `columns`.

    `columns` is every core column plus embedding_source_hash plus the
    embedding columns -- handler.py uses this both to skip re-embedding rows
    whose embeddable fields haven't changed (embedding_source_hash) and to
    skip re-upserting rows that haven't changed at all (a full comparison
    against every other column).
    """
    select_columns = [pk_column, *columns]
    cursor = connection.cursor()
    cursor.execute(f"SELECT {', '.join(select_columns)} FROM public.{table}")
    existing = {}
    for record in cursor.fetchall():
        row = dict(zip(select_columns, record))
        existing[row[pk_column]] = row
    cursor.close()
    return existing


def _values_equal(new_value, old_value) -> bool:
    """Compare a raw silver-JSON value against its stored Postgres value.

    The two sides arrive in different shapes for the same logical value --
    e.g. an ISO timestamp string vs. a datetime, or an int/str vs. a
    Decimal -- so a plain `==` would report a change on every row, every
    run. Values are coerced to the type Postgres already returned before
    comparing.
    """
    if new_value is None or old_value is None:
        return new_value == old_value
    if isinstance(old_value, (datetime, date)):
        try:
            parsed = datetime.fromisoformat(str(new_value))
        except ValueError:
            return str(old_value) == str(new_value)
        return old_value == parsed or old_value == parsed.date()
    if isinstance(old_value, Decimal):
        try:
            return old_value == Decimal(str(new_value))
        except (ValueError, ArithmeticError):
            return str(old_value) == str(new_value)
    return str(old_value) == str(new_value)


def select_changed(rows: list[dict[str, Any]], pk_field: str, existing: dict, compare_columns: tuple[str, ...]) -> list[dict[str, Any]]:
    """Drop rows that are identical to what's already stored.

    This -- not a SQL-side WHERE guard -- is what keeps upsert() from
    rewriting the whole table every run: glue_transform.py republishes the
    entire silver dataset on every Glue run, not a delta, so without this
    filter every row (including the vast majority that didn't change) would
    get re-sent to Postgres. Comparing here in Python instead of in SQL
    matters because these tables carry vector(512) TOAST columns -- doing
    the comparison server-side means Postgres has to detoast and compare
    them for every row, every run, which was slow enough to push a full run
    past the Lambda timeout. `compare_columns` should be the core
    (non-embedding) columns, including embedding_source_hash: a row whose
    embeddable text changed has a different hash and is caught here too, so
    the caller's re-embedding logic never needs to consider a row this
    function drops.
    """
    changed = []
    for row in rows:
        prior = existing.get(row[pk_field])
        if prior is None or any(not _values_equal(row.get(col), prior.get(col)) for col in compare_columns):
            changed.append(row)
    return changed


def estimate_upload_size_bytes(
    rows: list[dict[str, Any]],
    columns: tuple[str, ...],
    embedding_columns: tuple[str, ...],
    embedding_dimension: int,
) -> int:
    """Rough byte-size estimate of what upsert() would send for these rows.

    Embedding columns are sized from embedding_dimension rather than actual
    values, so this can run before embeddings.attach_embeddings() fills them
    in -- letting the caller gate the Cohere calls and the DB write on a
    single size check instead of paying for embeddings only to discard them.
    """
    per_row_embedding_bytes = len(embedding_columns) * embedding_dimension * BYTES_PER_EMBEDDING_DIMENSION
    total = per_row_embedding_bytes * len(rows)
    for row in rows:
        for column in columns:
            value = row.get(column)
            if value is not None:
                total += len(str(value).encode("utf-8"))
    return total


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
    # No WHERE guard here on purpose: `rows` is expected to already be
    # filtered down to genuinely new/changed rows by the caller, via
    # select_changed() above. An earlier version of this guard lived here as
    # a SQL `WHERE (t.*) IS DISTINCT FROM (EXCLUDED.*)` clause, but comparing
    # the vector(512) TOAST columns server-side for every row, every run, was
    # slow enough to push a full run past the Lambda timeout -- filtering in
    # Python means Postgres only ever sees rows that actually need writing.
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
        logger.debug("upserted batch %d-%d into public.%s", start, start + len(batch), table)
    connection.commit()
    cursor.close()
    logger.info("upserted %d row(s) into public.%s", len(rows), table)


def update_core_only(
    connection,
    table: str,
    rows: list[dict[str, Any]],
    pk_column: str,
    columns: tuple[str, ...],
) -> None:
    """Update only the core (non-embedding) columns of rows that are known to
    already exist.

    For rows whose embedding_source_hash is unchanged, upsert() would still
    put every embedding column in its UPDATE SET clause -- EXCLUDED.col is
    part of the proposed INSERT row regardless of whether it's ultimately
    used, so Postgres re-toasts a fresh copy of every vector(512) value on
    every such row, every run, even though the value is byte-identical to
    what's already stored. That unconditional rewrite (not undetected dead
    tuples -- autovacuum was keeping those at 0) is what inflated
    order_test/tonnage_test's on-disk size after the diffing logic's first
    run against previously-unconditionally-upserted data. This function's
    SQL never names an embedding column at all, so Postgres never touches
    their stored TOAST data for these rows.
    """
    if not rows:
        return

    update_columns = [col for col in columns if col != pk_column]
    assignments = ", ".join(f"{col} = %s" for col in update_columns)
    sql = f"UPDATE public.{table} SET {assignments} WHERE {pk_column} = %s"

    cursor = connection.cursor()
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start : start + BATCH_SIZE]
        params = [[row.get(col) for col in update_columns] + [row[pk_column]] for row in batch]
        cursor.executemany(sql, params)
        logger.debug("core-only updated batch %d-%d in public.%s", start, start + len(batch), table)
    connection.commit()
    cursor.close()
    logger.info("core-only updated %d row(s) in public.%s", len(rows), table)
