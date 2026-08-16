"""AWS Lambda: the silver -> gold loader for public.orders / public.tonnage.

Triggered directly by an EventBridge rule (``GoldUploadRule`` in
``infra/smu_daily_pipeline.yaml``) whenever ``glue_transform.py`` writes
``orders.json`` or ``tonnage.json`` to the silver bucket. The payload is a
single "Object Created" event, not a batch -- EventBridge invokes a direct
Lambda target once per matching event, unlike the SQS-fed bronze trigger.

Every column in the silver JSON is upserted into the matching gold table
(same names ``ai_platform/backend/tables/orders.py`` / ``tonnage.py`` and the
chat agent already expect), and each non-filterable text/set column gets a
``<column>_embedding`` sibling column computed with Cohere. So 15 silver
columns for orders (6 of them embedded) means gold has 15 + 6 = 21 data
columns, plus bookkeeping (``embedding_hashes``).

Row identity:
  - orders: ``order_id`` -- already a stable per-enquiry key
    (``glue_transform.py`` hashes one when bronze doesn't supply it).
  - tonnage: a row is a reported *position*, not a vessel (11,105 rows over
    1,037 vessels), so ``vessel_id`` alone can't be the primary key. The gold
    primary key is ``(vessel_id, open_date_start, open_date_end,
    first_date_received)`` -- exactly ``glue_transform.py``'s own dedup key
    for the tonnage silver file. Confirmed against the real source workbook:
    none of those 4 columns are ever null, and there are exactly 11,105
    distinct combinations, matching the published row count.

Cost control: base columns are cheap to always overwrite, so every row in the
file is upserted every run. Embeddings are not cheap (external API, rate
limited), so ``embedding_hashes`` on each row is a ``{column: sha256}`` map --
only columns whose hash changed since last run get re-embedded, so a Glue run
that only touched a few rows doesn't re-embed the whole table.

Runtime budget: a fresh deploy has to backfill ~13k existing rows. Row
upserts are batched (fast, cheap, idempotent -- safe to redo on a resume) but
Cohere calls are not, so the handler tracks its own remaining time and, if
work is left when the buffer is hit, asynchronously re-invokes itself with the
same event. This terminates because rows already embedded no longer show up
as "changed" on the next pass -- the same re-derive-from-source idempotency
the bronze trigger Lambda uses against its DynamoDB ledger, just checked
against ``embedding_hashes`` instead.

Uses ``pg8000`` rather than the app's ``asyncpg`` (see ``ai_platform/backend/db.py``)
purely for Lambda packaging: pg8000 is pure Python, so the deploy zip needs no
compiled/manylinux wheel. Cohere is called directly via ``urllib.request``
rather than the ``cohere`` SDK, to keep the zip to just that one dependency.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

import boto3
import pg8000.native

logger = logging.getLogger()
logger.setLevel(logging.INFO)

COHERE_EMBED_URL = "https://api.cohere.com/v1/embed"
COHERE_MODEL = "embed-english-v3.0"
COHERE_BATCH_SIZE = 90

# sha256 of a token that can never appear in real column text, so "known
# empty" has a stable hash distinct from "never checked" (key simply absent).
EMPTY_HASH = hashlib.sha256(b"\x00EMPTY\x00").hexdigest()

REMAINING_TIME_BUFFER_MS = 60_000
BATCH_SIZE = 200  # rows per bulk upsert, and per existing-hashes lookup query

_EMPTY_TOKENS = {"", "nan", "none", "nat", "<na>"}


@dataclass(frozen=True)
class ColumnSpec:
    """One silver column mapped onto one gold column.

    Attributes
    ----------
    json_key : str
        Key in the silver NDJSON row. Usually equal to ``pg_column``, except
        tonnage's DWT, which ``glue_transform.py`` writes as ``"DWT"``.
    pg_column : str
        Column name in the gold table.
    sql_type : str
        ``"text"``, ``"bigint"``, ``"double precision"``, ``"date"``, or
        ``"timestamp"``. Non-key values are always bound as text and cast in
        SQL (``:param::date`` etc.), so Postgres does the parsing. Primary key
        components are the exception -- see ``compute_pk``.
    """

    json_key: str
    pg_column: str
    sql_type: str


@dataclass(frozen=True)
class TableConfig:
    """Everything needed to sync one silver file into one gold table."""

    pk_columns: tuple[str, ...]  # pg_column names; each must also be in `columns`
    columns: tuple[ColumnSpec, ...]  # every gold data column, including pk ones
    embed_columns: tuple[str, ...]  # pg_column names, subset of `columns`

    def spec(self, pg_column: str) -> ColumnSpec:
        for c in self.columns:
            if c.pg_column == pg_column:
                return c
        raise KeyError(pg_column)


ORDERS_CONFIG = TableConfig(
    pk_columns=("order_id",),
    columns=(
        ColumnSpec("order_id", "order_id", "bigint"),
        ColumnSpec("date_received", "date_received", "date"),
        ColumnSpec("update_date", "update_date", "timestamp"),
        ColumnSpec("laycan_start", "laycan_start", "date"),
        ColumnSpec("laycan_end", "laycan_end", "date"),
        ColumnSpec("load_port", "load_port", "text"),
        ColumnSpec("discharge_port", "discharge_port", "text"),
        ColumnSpec("cargo_type", "cargo_type", "text"),
        ColumnSpec("cargo_description", "cargo_description", "text"),
        ColumnSpec("load_zone", "load_zone", "text"),
        ColumnSpec("discharge_parent_zone", "discharge_parent_zone", "text"),
        ColumnSpec("cargo_weight_min", "cargo_weight_min", "double precision"),
        ColumnSpec("cargo_weight_max", "cargo_weight_max", "double precision"),
        ColumnSpec("assigned", "assigned", "text"),
        ColumnSpec("assigned_vessel_name", "assigned_vessel_name", "text"),
    ),
    embed_columns=(
        "load_zone",
        "discharge_parent_zone",
        "load_port",
        "discharge_port",
        "cargo_type",
        "cargo_description",
    ),
)

TONNAGE_CONFIG = TableConfig(
    pk_columns=("vessel_id", "open_date_start", "open_date_end", "first_date_received"),
    columns=(
        ColumnSpec("update_date", "update_date", "timestamp"),
        ColumnSpec("parent_zone", "parent_zone", "text"),
        ColumnSpec("vessel_id", "vessel_id", "text"),
        ColumnSpec("vessel_status", "vessel_status", "text"),
        ColumnSpec("DWT", "dwt", "double precision"),  # silver key is "DWT", not "dwt"
        ColumnSpec("commercial_status", "commercial_status", "text"),
        ColumnSpec("ship_type", "ship_type", "text"),
        ColumnSpec("ship_size", "ship_size", "text"),
        ColumnSpec("ballast_laden", "ballast_laden", "text"),
        ColumnSpec("destination", "destination", "text"),
        ColumnSpec("open_area", "open_area", "text"),
        ColumnSpec("eta", "eta", "date"),
        ColumnSpec("open_date_start", "open_date_start", "date"),
        ColumnSpec("open_date_end", "open_date_end", "date"),
        ColumnSpec("first_date_received", "first_date_received", "date"),
        ColumnSpec("order_id", "order_id", "text"),
    ),
    embed_columns=(
        "parent_zone",
        "open_area",
        "destination",
        "ship_size",
        "ship_type",
        "vessel_status",
    ),
)

TABLES: dict[str, TableConfig] = {"orders": ORDERS_CONFIG, "tonnage": TONNAGE_CONFIG}


def categorize_key(key: str) -> str | None:
    """Map an S3 key to a gold table name.

    Mirrors ``categorize_input_file`` in ``scripts/glue_transform.py``: a
    plain substring match on the file name, not the full path.

    Parameters
    ----------
    key : str
        S3 object key, e.g. ``orders/orders.json``.

    Returns
    -------
    str or None
        ``"orders"``, ``"tonnage"``, or ``None`` if neither matches.
    """
    name = key.rsplit("/", 1)[-1].lower()
    if "tonnage" in name:
        return "tonnage"
    if "order" in name:
        return "orders"
    return None


def normalize_value(value: Any) -> str | None:
    """Prepare a raw silver value for binding as text, regardless of target SQL type.

    Everything is bound as text (or NULL) and cast in SQL -- Postgres parses
    numbers/dates from their string form, so this only needs to catch the
    various "empty" spellings pandas/JSON round-tripping produces.

    Parameters
    ----------
    value : Any

    Returns
    -------
    str or None
    """
    if value is None:
        return None
    text = str(value).strip()
    return None if text.lower() in _EMPTY_TOKENS else text


def compute_hash(value: Any) -> str:
    """Hash a source column value for embedding change detection.

    Parameters
    ----------
    value : Any
        Raw value from the silver JSON row.

    Returns
    -------
    str
        ``EMPTY_HASH`` for null/blank values, else a sha256 hex digest of the
        stripped string form.
    """
    text = normalize_value(value)
    if not text:
        return EMPTY_HASH
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_key_component(text: str, sql_type: str) -> Any:
    """Parse a primary-key column's raw text into the type Postgres will hand back.

    Primary key values double as Python dict keys (to diff this run's rows
    against ``embedding_hashes`` already stored for them) and get compared
    against rows *fetched back* from Postgres, which returns native types
    (``int`` for bigint, ``datetime.date`` for date), not JSON strings -- and
    pandas' ``to_json(date_format="iso")`` writes even a pure date as a full
    ISO timestamp (``"2026-01-01T00:00:00.000Z"``). Parsing both sides into
    the same native type is what makes the dict lookups actually match.

    Parameters
    ----------
    text : str
        Already non-empty (``normalize_value`` applied by the caller).
    sql_type : str

    Returns
    -------
    Any
        ``int`` for bigint, ``datetime.date`` for date, the string itself for
        text/timestamp.
    """
    if sql_type == "bigint":
        return int(text)
    if sql_type == "date":
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    return text


def compute_pk(config: TableConfig, row: dict[str, Any]) -> tuple[Any, ...] | None:
    """Compute a row's primary key tuple, or None if any component is missing.

    Parameters
    ----------
    config : TableConfig
    row : dict
        One parsed silver JSON row.

    Returns
    -------
    tuple or None
    """
    values = []
    for pg_column in config.pk_columns:
        spec = config.spec(pg_column)
        text = normalize_value(row.get(spec.json_key))
        if text is None:
            return None
        try:
            values.append(_parse_key_component(text, spec.sql_type))
        except ValueError:
            return None
    return tuple(values)


def read_ndjson_from_s3(s3_client, bucket: str, key: str) -> list[dict[str, Any]]:
    """Download and parse a newline-delimited JSON object.

    Parameters
    ----------
    s3_client : boto3 S3 client
    bucket : str
    key : str

    Returns
    -------
    list of dict
        One dict per non-blank line.
    """
    body = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
    return [json.loads(line) for line in body.splitlines() if line.strip()]


def cohere_embed(texts: list[str], api_key: str) -> list[list[float]]:
    """Embed a batch of texts with Cohere.

    Parameters
    ----------
    texts : list of str
        Up to ``COHERE_BATCH_SIZE`` non-empty strings.
    api_key : str
        Cohere API key.

    Returns
    -------
    list of list of float
        One vector per input text, same order.

    Raises
    ------
    RuntimeError
        If the API returns a non-2xx response after retries.
    """
    payload = json.dumps(
        {
            "model": COHERE_MODEL,
            "texts": texts,
            "input_type": "search_document",
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        COHERE_EMBED_URL,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = json.loads(response.read().decode("utf-8"))
            return body["embeddings"]
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            last_error = RuntimeError(f"Cohere embed failed ({exc.code}): {detail}")
            if exc.code not in (429, 500, 502, 503, 504):
                raise last_error from exc
        except urllib.error.URLError as exc:
            last_error = RuntimeError(f"Cohere embed request failed: {exc}")
        time.sleep(2**attempt)

    assert last_error is not None
    raise last_error


def chunked(items: list[Any], size: int) -> list[list[Any]]:
    """Split a list into fixed-size chunks, last one possibly shorter."""
    return [items[i : i + size] for i in range(0, len(items), size)]


@dataclass
class UpsertJob:
    """One unit of resumable work: bulk-upsert a chunk of full rows."""

    rows: list[dict[str, Any]]  # raw silver rows


@dataclass
class EmbedJob:
    """One unit of resumable work: embed or null-out one column on a set of rows."""

    column: str
    pks: list[tuple[Any, ...]]
    texts: list[str] | None  # None for a null-update job


def _cast_suffix(spec: ColumnSpec) -> str:
    return "" if spec.sql_type == "text" else f"::{spec.sql_type}"


def fetch_existing_hashes(
    conn: pg8000.native.Connection,
    table: str,
    config: TableConfig,
    pks: list[tuple[Any, ...]],
) -> dict[tuple[Any, ...], dict[str, str]]:
    """Look up ``embedding_hashes`` for a set of primary keys, batched.

    Uses a row-value ``IN`` list rather than ``= ANY(:pks)`` since the latter
    only works for a single-column key -- tonnage's is 4 columns.

    Parameters
    ----------
    conn : pg8000 connection
    table : str
    config : TableConfig
    pks : list of tuple
        Primary key tuples to look up.

    Returns
    -------
    dict
        ``{pk_tuple: {column: hash}}``, missing for pks not yet in the table.
    """
    if not pks:
        return {}

    pk_specs = [config.spec(col) for col in config.pk_columns]
    pk_cols_sql = ", ".join(config.pk_columns)
    result: dict[tuple[Any, ...], dict[str, str]] = {}

    for batch in chunked(pks, BATCH_SIZE):
        row_exprs = []
        params: dict[str, Any] = {}
        for i, pk in enumerate(batch):
            placeholders = []
            for j, spec in enumerate(pk_specs):
                pname = f"k{i}_{j}"
                params[pname] = pk[j]
                placeholders.append(f":{pname}{_cast_suffix(spec)}")
            row_exprs.append("(" + ", ".join(placeholders) + ")")

        sql = (
            f"SELECT {pk_cols_sql}, embedding_hashes FROM public.{table} "
            f"WHERE ({pk_cols_sql}) IN ({', '.join(row_exprs)})"
        )
        for record in conn.run(sql, **params):
            *key_parts, hashes = record
            result[tuple(key_parts)] = hashes or {}

    return result


def build_jobs(
    conn: pg8000.native.Connection,
    table: str,
    config: TableConfig,
    rows: list[dict[str, Any]],
) -> list[UpsertJob | EmbedJob]:
    """Build this run's work list: upsert every row, then re-embed changed columns.

    Parameters
    ----------
    conn : pg8000 connection
    table : str
    config : TableConfig
    rows : list of dict
        Parsed silver JSON rows.

    Returns
    -------
    list of UpsertJob or EmbedJob
        Upsert jobs first, so every embed job's row is guaranteed to already
        exist by the time it runs (jobs execute in list order).
    """
    row_by_pk: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        pk = compute_pk(config, row)
        if pk is not None:
            row_by_pk[pk] = row  # last occurrence in the file wins

    if not row_by_pk:
        return []

    jobs: list[UpsertJob | EmbedJob] = [
        UpsertJob(list(batch)) for batch in chunked(list(row_by_pk.values()), BATCH_SIZE)
    ]

    existing_hashes = fetch_existing_hashes(conn, table, config, list(row_by_pk.keys()))

    for column in config.embed_columns:
        json_key = config.spec(column).json_key
        changed_texts: list[tuple[tuple[Any, ...], str]] = []
        changed_nulls: list[tuple[Any, ...]] = []
        for pk, row in row_by_pk.items():
            value = row.get(json_key)
            new_hash = compute_hash(value)
            if new_hash == existing_hashes.get(pk, {}).get(column):
                continue  # unchanged, nothing to do
            text = normalize_value(value)
            if text:
                changed_texts.append((pk, text))
            else:
                changed_nulls.append(pk)

        for batch in chunked(changed_texts, COHERE_BATCH_SIZE):
            jobs.append(EmbedJob(column, [pk for pk, _ in batch], [t for _, t in batch]))
        for batch in chunked(changed_nulls, BATCH_SIZE):
            jobs.append(EmbedJob(column, list(batch), None))

    return jobs


def run_upsert_job(conn: pg8000.native.Connection, table: str, config: TableConfig, job: UpsertJob) -> None:
    """Bulk-upsert a chunk of full rows in one statement.

    Column names and casts come entirely from ``config`` (fixed, code-defined
    identifiers, never from JSON keys or user input) -- only values are bound
    as parameters, same posture as ``ClauseBuilder`` in
    ``ai_platform/backend/tables/base.py``.

    Parameters
    ----------
    conn : pg8000 connection
    table : str
    config : TableConfig
    job : UpsertJob

    Returns
    -------
    None
    """
    all_columns = ", ".join(c.pg_column for c in config.columns)
    non_pk = [c.pg_column for c in config.columns if c.pg_column not in config.pk_columns]
    set_clause = ", ".join(f"{name} = EXCLUDED.{name}" for name in non_pk)

    value_rows: list[str] = []
    params: dict[str, Any] = {}
    for i, row in enumerate(job.rows):
        placeholders = []
        for c in config.columns:
            param_name = f"c{i}_{c.pg_column}"
            params[param_name] = normalize_value(row.get(c.json_key))
            placeholders.append(f":{param_name}{_cast_suffix(c)}")
        value_rows.append("(" + ", ".join(placeholders) + ")")

    sql = (
        f"INSERT INTO public.{table} ({all_columns}) VALUES "
        + ", ".join(value_rows)
        + f" ON CONFLICT ({', '.join(config.pk_columns)}) DO UPDATE SET {set_clause}"
    )
    conn.run(sql, **params)


def run_embed_job(
    conn: pg8000.native.Connection,
    table: str,
    config: TableConfig,
    job: EmbedJob,
    api_key: str,
) -> None:
    """Execute one job: embed (or null) a column for a batch of rows.

    Each row is updated individually so a partially-completed batch still
    leaves ``embedding_hashes`` consistent with what was actually written.

    Parameters
    ----------
    conn : pg8000 connection
    table : str
    config : TableConfig
    job : EmbedJob
    api_key : str
        Cohere API key. Unused for null jobs.

    Returns
    -------
    None
    """
    embed_col = f"{job.column}_embedding"
    pk_specs = [config.spec(col) for col in config.pk_columns]

    def where_clause(pk: tuple[Any, ...]) -> tuple[str, dict[str, Any]]:
        clauses = []
        params: dict[str, Any] = {}
        for i, (col, spec) in enumerate(zip(config.pk_columns, pk_specs)):
            pname = f"pk{i}"
            params[pname] = pk[i]
            clauses.append(f"{col} = :{pname}{_cast_suffix(spec)}")
        return " AND ".join(clauses), params

    if job.texts is None:
        for pk in job.pks:
            where_sql, where_params = where_clause(pk)
            conn.run(
                f"UPDATE public.{table} "
                f"SET {embed_col} = NULL::vector, "
                f"    embedding_hashes = embedding_hashes || :patch::jsonb "
                f"WHERE {where_sql}",
                patch=json.dumps({job.column: EMPTY_HASH}),
                **where_params,
            )
        return

    vectors = cohere_embed(job.texts, api_key)
    for pk, text, vector in zip(job.pks, job.texts, vectors):
        where_sql, where_params = where_clause(pk)
        conn.run(
            f"UPDATE public.{table} "
            f"SET {embed_col} = :vector::vector, "
            f"    embedding_hashes = embedding_hashes || :patch::jsonb "
            f"WHERE {where_sql}",
            vector="[" + ",".join(repr(v) for v in vector) + "]",
            patch=json.dumps({job.column: compute_hash(text)}),
            **where_params,
        )


def run_job(conn: pg8000.native.Connection, table: str, config: TableConfig, job: UpsertJob | EmbedJob, api_key: str) -> None:
    """Dispatch one job to the right executor."""
    if isinstance(job, UpsertJob):
        run_upsert_job(conn, table, config, job)
    else:
        run_embed_job(conn, table, config, job, api_key)


def connect(dsn: str) -> pg8000.native.Connection:
    """Open a pg8000 connection from a ``postgresql://`` DSN.

    ``pg8000.native`` takes discrete kwargs rather than a URL, so this parses
    ``SUPABASE_DB_URL`` (same variable ``ai_platform/backend/db.py`` reads).

    Parameters
    ----------
    dsn : str
        A ``postgresql://user:password@host:port/dbname`` string.

    Returns
    -------
    pg8000.native.Connection
    """
    parts = urlsplit(dsn.replace("postgresql+asyncpg://", "postgresql://"))
    return pg8000.native.Connection(
        user=parts.username or "",
        password=parts.password,
        host=parts.hostname,
        port=parts.port or 5432,
        database=parts.path.lstrip("/"),
        ssl_context=True,
    )


def get_remaining_ms(context: Any) -> int:
    """Read the Lambda invocation's remaining time, defaulting generously when absent (local runs)."""
    getter = getattr(context, "get_remaining_time_in_millis", None)
    return getter() if getter else 10**9


def reinvoke_self(context: Any, event: dict[str, Any]) -> None:
    """Asynchronously re-invoke this function with the same event.

    Parameters
    ----------
    context : Lambda context
    event : dict
        The triggering event, forwarded unchanged; the next invocation
        re-diffs against ``embedding_hashes`` and only picks up leftover work.

    Returns
    -------
    None
    """
    boto3.client("lambda").invoke(
        FunctionName=context.invoked_function_arn,
        InvocationType="Event",
        Payload=json.dumps(event).encode("utf-8"),
    )


def process_file(
    conn: pg8000.native.Connection,
    s3_client,
    bucket: str,
    key: str,
    api_key: str,
    context: Any,
    event: dict[str, Any],
) -> None:
    """Sync one silver JSON file into its gold table, time-boxed.

    Parameters
    ----------
    conn : pg8000 connection
    s3_client : boto3 S3 client
    bucket, key : str
        Location of the silver file that triggered this run.
    api_key : str
        Cohere API key.
    context : Lambda context
        Used to check remaining time and, if needed, re-invoke.
    event : dict
        Forwarded to ``reinvoke_self`` unchanged if work is left over.

    Returns
    -------
    None
    """
    table = categorize_key(key)
    if table is None:
        logger.info("Ignoring s3://%s/%s: does not match orders or tonnage", bucket, key)
        return

    config = TABLES[table]
    rows = read_ndjson_from_s3(s3_client, bucket, key)
    jobs = build_jobs(conn, table, config, rows)
    upsert_jobs = sum(1 for j in jobs if isinstance(j, UpsertJob))
    embed_jobs = len(jobs) - upsert_jobs

    logger.info(
        "s3://%s/%s -> public.%s: %d row(s), %d upsert job(s), %d embed job(s) queued",
        bucket,
        key,
        table,
        len(rows),
        upsert_jobs,
        embed_jobs,
    )

    completed = 0
    for job in jobs:
        if get_remaining_ms(context) < REMAINING_TIME_BUFFER_MS:
            logger.info(
                "Time budget reached after %d/%d job(s); re-invoking to finish the rest",
                completed,
                len(jobs),
            )
            reinvoke_self(context, event)
            return
        run_job(conn, table, config, job, api_key)
        completed += 1

    logger.info("public.%s: finished all %d job(s)", table, len(jobs))


def handler(event: dict[str, Any], context: Any) -> None:
    """Lambda entry point.

    Parameters
    ----------
    event : dict
        A single EventBridge "Object Created" event
        (``event["detail"]["bucket"]["name"]`` / ``event["detail"]["object"]["key"]``).
    context : Lambda context

    Returns
    -------
    None
    """
    detail = event.get("detail", {})
    bucket = detail.get("bucket", {}).get("name")
    key = detail.get("object", {}).get("key")
    if not bucket or not key:
        logger.warning("Event carried no bucket/key, ignoring: %s", event)
        return

    dsn = os.environ["SUPABASE_DB_URL"]
    api_key = os.environ["COHERE_API_KEY"]

    conn = connect(dsn)
    try:
        process_file(conn, boto3.client("s3"), bucket, key, api_key, context, event)
    finally:
        conn.close()
