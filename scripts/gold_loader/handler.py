"""Lambda entrypoint: silver JSON -> embedded rows -> Supabase gold tables.

Triggered by infra/smu_gold_loader.yaml's EventBridge rule on the
smu-glue-transform Glue job's SUCCEEDED state. The event payload is ignored
-- like the existing TriggerLambda (infra/smu_daily_pipeline.yaml), this
always re-reads the current silver files in full, since glue_transform.py's
read-modify-write means every Glue run republishes the whole dataset, not
just a delta -- so every order/tonnage row here needs to be checked against
what gold already has.

That check is done entirely against DynamoDB (run_tracker's
get_row_hashes/put_row_hashes), never by SELECTing gold: every row's core
columns and embeddable-fields hash are looked up in DynamoDB, so Postgres is
only ever written to (upsert/update_core_only), never read from, and only
rows that actually changed get sent at all. If nothing changed anywhere,
the run exits before opening a Supabase connection or calling Cohere.

Deployed with the "gold_loader" package kept intact in the zip (not
flattened) -- see infra/smu_gold_loader.yaml's build step -- so these stay
relative imports and the same handler works both deployed
(gold_loader.handler.handler) and locally as scripts.gold_loader.handler.
"""

import hashlib
import os

import boto3

from . import db, embeddings, run_tracker, silver_reader
from .logging_utils import get_logger

logger = get_logger("gold_loader")

ORDERS_COLUMNS = (
    "order_id",
    "date_received",
    "update_date",
    "laycan_start",
    "laycan_end",
    "load_port",
    "discharge_port",
    "cargo_type",
    "cargo_description",
    "load_zone",
    "discharge_parent_zone",
    "cargo_weight_min",
    "cargo_weight_max",
    "assigned",
    "embedding_source_hash",
)

TONNAGE_COLUMNS = (
    "tonnage_row_key",
    "vessel_id",
    "update_date",
    "parent_zone",
    "vessel_status",
    "dwt",
    "commercial_status",
    "ship_type",
    "ship_size",
    "ballast_laden",
    "destination",
    "open_area",
    "eta",
    "open_date_start",
    "open_date_end",
    "first_date_received",
    "order_id",
    "embedding_source_hash",
)

ORDERS_EMBEDDING_COLUMNS = tuple(f"{field}_embedding" for field in embeddings.ORDERS_EMBED_FIELDS)
TONNAGE_EMBEDDING_COLUMNS = tuple(f"{field}_embedding" for field in embeddings.TONNAGE_EMBED_FIELDS)
MAX_UPLOAD_BYTES = 495 * 1024 * 1024

ORDER_ROW_KEY_PREFIX = "order#"
TONNAGE_ROW_KEY_PREFIX = "tonnage#"


def _tonnage_row_key(row: dict) -> str:
    """Deterministic PK for tonnage_test, standing in for the composite
    (vessel_id, open_date_start, open_date_end, first_date_received) key
    glue_transform.py's write_dataset() dedups tonnage on. A hash sidesteps
    NULL-ability issues a raw composite primary key would have -- those date
    fields can be null (see transform_tonnage()'s select_or_null() mapping).
    """
    fields = ("vessel_id", "open_date_start", "open_date_end", "first_date_received")
    joined = "|".join(str(row.get(field) or "") for field in fields)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _as_bigint(value):
    return int(value) if value not in (None, "") else None


def _select_changed(rows: list[dict], columns: tuple[str, ...], existing: dict) -> list[dict]:
    """Drop rows that are identical to what DynamoDB says gold already has.

    Caches the freshly computed hash on each row as "_row_hash" so the later
    DynamoDB write-back doesn't need to hash the row a second time. `columns`
    already ends in embedding_source_hash (see ORDERS_COLUMNS/TONNAGE_COLUMNS),
    so a row whose embeddable text changed necessarily shows up as changed
    here too -- the caller's re-embedding logic (_select_stale) never needs
    to consider a row this function drops.
    """
    changed = []
    for row in rows:
        row["_row_hash"] = run_tracker.row_hash(row, columns)
        prior = existing.get(row["_dynamo_key"])
        if prior is None or prior[run_tracker.ROW_HASH_ATTR] != row["_row_hash"]:
            changed.append(row)
    return changed


def _select_stale(rows: list[dict], existing: dict) -> tuple[list[dict], list[dict]]:
    """Partition changed rows into (stale, core_only).

    `stale` rows are new or have a changed embedding_source_hash -- they need
    a Cohere call and go through db.upsert(), which writes every column
    including the vector(512) ones. `core_only` rows matched their stored
    hash -- their embeddable fields didn't change, so they skip the API call
    and go through db.update_core_only() instead of upsert(): that function's
    SQL never names an embedding column, so Postgres never re-toasts their
    already-correct vector data. Routing core_only rows through upsert()
    instead (as this used to) is what inflated order_test/tonnage_test's
    on-disk size on every run with changed rows, even when nothing needed
    re-embedding.
    """
    stale = []
    core_only = []
    for row in rows:
        prior = existing.get(row["_dynamo_key"])
        if prior is None or prior[run_tracker.EMBEDDING_HASH_ATTR] != row["embedding_source_hash"]:
            stale.append(row)
        else:
            core_only.append(row)
    return stale, core_only


def handler(event, context):
    logger.info("gold_loader run starting")

    silver_bucket = os.environ["SILVER_BUCKET_NAME"]
    silver_prefix = os.environ.get("SILVER_PREFIX", "")
    orders_table = os.environ.get("ORDERS_TABLE_NAME", "order_test")
    tonnage_table = os.environ.get("TONNAGE_TABLE_NAME", "tonnage_test")
    cohere_api_key = os.environ["COHERE_API_KEY"]
    cohere_model = os.environ.get("COHERE_MODEL", "embed-v4.0")
    embedding_dimension = int(os.environ.get("EMBEDDING_DIMENSION", "512"))
    hash_table = os.environ["GOLD_PROCESSED_HASH_TABLE"]

    logger.info("reading silver files from s3://%s/%s", silver_bucket, silver_prefix)
    order_rows = silver_reader.read_orders(silver_bucket, silver_prefix)
    tonnage_rows = silver_reader.read_tonnage(silver_bucket, silver_prefix)
    logger.info("read %d order rows and %d tonnage rows", len(order_rows), len(tonnage_rows))

    dynamodb_client = boto3.client("dynamodb")

    for row in order_rows:
        row["order_id"] = _as_bigint(row.get("order_id"))
        row["embedding_source_hash"] = embeddings.source_hash(row, embeddings.ORDERS_EMBED_FIELDS)
        row["_dynamo_key"] = f"{ORDER_ROW_KEY_PREFIX}{row['order_id']}"

    for row in tonnage_rows:
        row["tonnage_row_key"] = _tonnage_row_key(row)
        row["order_id"] = _as_bigint(row.get("order_id"))
        row["embedding_source_hash"] = embeddings.source_hash(row, embeddings.TONNAGE_EMBED_FIELDS)
        row["_dynamo_key"] = f"{TONNAGE_ROW_KEY_PREFIX}{row['tonnage_row_key']}"

    # Row-level dedup lives entirely in DynamoDB -- no SELECT against Supabase
    # at any point. glue_transform.py republishes the whole silver dataset
    # every run, so without this, every row here (not just the genuinely new
    # or changed ones) would get sent to Postgres.
    all_keys = [row["_dynamo_key"] for row in order_rows] + [row["_dynamo_key"] for row in tonnage_rows]
    existing_row_hashes = run_tracker.get_row_hashes(dynamodb_client, hash_table, all_keys)
    logger.info(
        "found %d/%d row hash(es) already recorded in DynamoDB",
        len(existing_row_hashes),
        len(all_keys),
    )

    changed_orders = _select_changed(order_rows, ORDERS_COLUMNS[1:], existing_row_hashes)
    changed_tonnage = _select_changed(tonnage_rows, TONNAGE_COLUMNS[1:], existing_row_hashes)
    logger.info(
        "%d/%d order rows and %d/%d tonnage rows are new or changed",
        len(changed_orders),
        len(order_rows),
        len(changed_tonnage),
        len(tonnage_rows),
    )

    if not changed_orders and not changed_tonnage:
        # Every row's hash already matches DynamoDB -- exit before opening a
        # Supabase connection or calling Cohere at all.
        summary = {
            "skipped": True,
            "reason": "no order or tonnage rows changed since the last successful gold load",
        }
        logger.info("gold_loader summary: %s", summary)
        return summary

    stale_orders, core_only_orders = _select_stale(changed_orders, existing_row_hashes)
    stale_tonnage, core_only_tonnage = _select_stale(changed_tonnage, existing_row_hashes)
    logger.info(
        "%d/%d changed order rows and %d/%d changed tonnage rows need re-embedding",
        len(stale_orders),
        len(changed_orders),
        len(stale_tonnage),
        len(changed_tonnage),
    )

    # Embedding bytes are only estimated for stale rows -- core_only rows
    # never carry embedding columns through to Postgres at all (see
    # db.update_core_only()), so they'd inflate this estimate for no reason.
    orders_size = db.estimate_upload_size_bytes(
        changed_orders, ORDERS_COLUMNS, (), embedding_dimension
    ) + db.estimate_upload_size_bytes(stale_orders, (), ORDERS_EMBEDDING_COLUMNS, embedding_dimension)
    tonnage_size = db.estimate_upload_size_bytes(
        changed_tonnage, TONNAGE_COLUMNS, (), embedding_dimension
    ) + db.estimate_upload_size_bytes(stale_tonnage, (), TONNAGE_EMBEDDING_COLUMNS, embedding_dimension)
    total_size = orders_size + tonnage_size
    logger.info("estimated upload size: %.1f MB", total_size / (1024 * 1024))
    if total_size > MAX_UPLOAD_BYTES:
        summary = {
            "skipped": True,
            "reason": (
                f"estimated upload size {total_size / (1024 * 1024):.1f} MB exceeds the "
                f"{MAX_UPLOAD_BYTES / (1024 * 1024):.0f} MB limit"
            ),
            "orders_estimated_mb": round(orders_size / (1024 * 1024), 1),
            "tonnage_estimated_mb": round(tonnage_size / (1024 * 1024), 1),
            "orders_changed": len(changed_orders),
            "tonnage_changed": len(changed_tonnage),
        }
        logger.warning("gold_loader summary: %s", summary)
        return summary

    embeddings.attach_embeddings(
        stale_orders, embeddings.ORDERS_EMBED_FIELDS, cohere_api_key, cohere_model, embedding_dimension
    )
    embeddings.attach_embeddings(
        stale_tonnage, embeddings.TONNAGE_EMBED_FIELDS, cohere_api_key, cohere_model, embedding_dimension
    )

    # Supabase is only ever opened here, once there's real writing to do --
    # every read needed to get to this point came from DynamoDB above.
    logger.info("connecting to Supabase")
    connection = db.connect(os.environ["SUPABASE_DB_URL"])
    try:
        logger.info(
            "upserting %d order rows (with embeddings) and core-only updating %d into %s",
            len(stale_orders),
            len(core_only_orders),
            orders_table,
        )
        db.upsert(connection, orders_table, stale_orders, "order_id", ORDERS_COLUMNS, ORDERS_EMBEDDING_COLUMNS)
        db.update_core_only(connection, orders_table, core_only_orders, "order_id", ORDERS_COLUMNS)

        logger.info(
            "upserting %d tonnage rows (with embeddings) and core-only updating %d into %s",
            len(stale_tonnage),
            len(core_only_tonnage),
            tonnage_table,
        )
        db.upsert(
            connection, tonnage_table, stale_tonnage, "tonnage_row_key", TONNAGE_COLUMNS, TONNAGE_EMBEDDING_COLUMNS
        )
        db.update_core_only(connection, tonnage_table, core_only_tonnage, "tonnage_row_key", TONNAGE_COLUMNS)
    finally:
        connection.close()

    # Only record success once the upsert has actually completed -- if the
    # DB write fails, the run should still look "stale" next time.
    changed_row_hashes = {
        row["_dynamo_key"]: {
            run_tracker.ROW_HASH_ATTR: row["_row_hash"],
            run_tracker.EMBEDDING_HASH_ATTR: row["embedding_source_hash"],
        }
        for row in (*stale_orders, *core_only_orders, *stale_tonnage, *core_only_tonnage)
    }
    if changed_row_hashes:
        run_tracker.put_row_hashes(dynamodb_client, hash_table, changed_row_hashes)

    summary = {
        "orders_total": len(order_rows),
        "orders_changed": len(changed_orders),
        "orders_embedded": len(stale_orders),
        "tonnage_total": len(tonnage_rows),
        "tonnage_changed": len(changed_tonnage),
        "tonnage_embedded": len(stale_tonnage),
    }
    logger.info("gold_loader summary: %s", summary)
    return summary