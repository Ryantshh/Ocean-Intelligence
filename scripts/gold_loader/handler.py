"""Lambda entrypoint: silver JSON -> embedded rows -> Supabase gold tables.

Triggered by infra/smu_gold_loader.yaml's EventBridge rule on the
smu-glue-transform Glue job's SUCCEEDED state. The event payload is ignored
-- like the existing TriggerLambda (infra/smu_daily_pipeline.yaml), this
always re-reads the current silver files in full, since glue_transform.py's
read-modify-write means every Glue run republishes the whole dataset, not
just a delta.

Before doing any real work, checks a DynamoDB record of each silver file's
content hash from the last successful gold load. If both orders.json and
tonnage.json are byte-identical to what was already loaded, the run exits
immediately -- no Supabase connection, no Cohere calls. This is a file-level
complement to _select_stale()'s row-level dedup below: this check guards
against paying for a Supabase round-trip on a fully no-op run (e.g. a Glue
run that only touched one of the two datasets), while _select_stale() still
protects the embedding API cost within a run that does have real changes.

Deployed with the "gold_loader" package kept intact in the zip (not
flattened) -- see infra/smu_gold_loader.yaml's build step -- so these stay
relative imports and the same handler works both deployed
(gold_loader.handler.handler) and locally as scripts.gold_loader.handler.
"""

import hashlib
import os

import boto3

from . import db, embeddings, silver_reader

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
    "assigned_vessel_name",
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


def _select_stale(rows: list[dict], pk_field: str, existing: dict) -> list[dict]:
    """Split off rows whose embeddable fields are new or changed.

    Rows whose embedding_source_hash matches what's already stored have
    their embedding columns filled in from the existing row in place, so
    they're upserted (core columns may still have changed) without an
    embedding API call.
    """
    stale = []
    for row in rows:
        prior = existing.get(row[pk_field])
        if prior is None or prior["embedding_source_hash"] != row["embedding_source_hash"]:
            stale.append(row)
        else:
            for column, value in prior.items():
                if column.endswith("_embedding"):
                    row[column] = value
    return stale


def _content_hash(raw_bytes: bytes) -> str:
    return hashlib.sha256(raw_bytes).hexdigest()


def _get_last_hash(dynamodb_client, table_name: str, source_key: str) -> str | None:
    response = dynamodb_client.get_item(
        TableName=table_name,
        Key={"source_key": {"S": source_key}},
        ConsistentRead=True,
    )
    item = response.get("Item")
    return item["content_hash"]["S"] if item else None


def _set_last_hash(dynamodb_client, table_name: str, source_key: str, content_hash: str) -> None:
    dynamodb_client.put_item(
        TableName=table_name,
        Item={
            "source_key": {"S": source_key},
            "content_hash": {"S": content_hash},
        },
    )


def handler(event, context):
    silver_bucket = os.environ["SILVER_BUCKET_NAME"]
    silver_prefix = os.environ.get("SILVER_PREFIX", "")
    orders_table = os.environ.get("ORDERS_TABLE_NAME", "order_test")
    tonnage_table = os.environ.get("TONNAGE_TABLE_NAME", "tonnage_test")
    cohere_api_key = os.environ["COHERE_API_KEY"]
    cohere_model = os.environ.get("COHERE_MODEL", "embed-v4.0")
    embedding_dimension = int(os.environ.get("EMBEDDING_DIMENSION", "512"))
    hash_table = os.environ["GOLD_PROCESSED_HASH_TABLE"]

    orders_raw, order_rows = silver_reader.read_orders(silver_bucket, silver_prefix)
    tonnage_raw, tonnage_rows = silver_reader.read_tonnage(silver_bucket, silver_prefix)

    orders_hash = _content_hash(orders_raw)
    tonnage_hash = _content_hash(tonnage_raw)
    orders_source_key = f"s3://{silver_bucket}/{silver_prefix}/orders/orders.json"
    tonnage_source_key = f"s3://{silver_bucket}/{silver_prefix}/tonnage/tonnage.json"

    dynamodb_client = boto3.client("dynamodb")
    if (
        _get_last_hash(dynamodb_client, hash_table, orders_source_key) == orders_hash
        and _get_last_hash(dynamodb_client, hash_table, tonnage_source_key) == tonnage_hash
    ):
        summary = {
            "skipped": True,
            "reason": "silver files unchanged since last successful gold load",
        }
        print(f"gold_loader summary: {summary}")
        return summary

    for row in order_rows:
        row["order_id"] = _as_bigint(row.get("order_id"))
        row["embedding_source_hash"] = embeddings.source_hash(row, embeddings.ORDERS_EMBED_FIELDS)

    for row in tonnage_rows:
        row["tonnage_row_key"] = _tonnage_row_key(row)
        row["order_id"] = _as_bigint(row.get("order_id"))
        row["embedding_source_hash"] = embeddings.source_hash(row, embeddings.TONNAGE_EMBED_FIELDS)

    orders_size = db.estimate_upload_size_bytes(
        order_rows, ORDERS_COLUMNS, ORDERS_EMBEDDING_COLUMNS, embedding_dimension
    )
    tonnage_size = db.estimate_upload_size_bytes(
        tonnage_rows, TONNAGE_COLUMNS, TONNAGE_EMBEDDING_COLUMNS, embedding_dimension
    )
    total_size = orders_size + tonnage_size
    if total_size > MAX_UPLOAD_BYTES:
        summary = {
            "skipped": True,
            "reason": (
                f"estimated upload size {total_size / (1024 * 1024):.1f} MB exceeds the "
                f"{MAX_UPLOAD_BYTES / (1024 * 1024):.0f} MB limit"
            ),
            "orders_estimated_mb": round(orders_size / (1024 * 1024), 1),
            "tonnage_estimated_mb": round(tonnage_size / (1024 * 1024), 1),
            "orders_total": len(order_rows),
            "tonnage_total": len(tonnage_rows),
        }
        print(f"gold_loader summary: {summary}")
        return summary

    connection = db.connect(os.environ["SUPABASE_DB_URL"])
    try:
        existing_orders = db.fetch_existing(connection, orders_table, "order_id", ORDERS_EMBEDDING_COLUMNS)
        existing_tonnage = db.fetch_existing(
            connection, tonnage_table, "tonnage_row_key", TONNAGE_EMBEDDING_COLUMNS
        )

        stale_orders = _select_stale(order_rows, "order_id", existing_orders)
        stale_tonnage = _select_stale(tonnage_rows, "tonnage_row_key", existing_tonnage)

        embeddings.attach_embeddings(
            stale_orders, embeddings.ORDERS_EMBED_FIELDS, cohere_api_key, cohere_model, embedding_dimension
        )
        embeddings.attach_embeddings(
            stale_tonnage, embeddings.TONNAGE_EMBED_FIELDS, cohere_api_key, cohere_model, embedding_dimension
        )

        db.upsert(connection, orders_table, order_rows, "order_id", ORDERS_COLUMNS, ORDERS_EMBEDDING_COLUMNS)
        db.upsert(
            connection, tonnage_table, tonnage_rows, "tonnage_row_key", TONNAGE_COLUMNS, TONNAGE_EMBEDDING_COLUMNS
        )
    finally:
        connection.close()

    # Only record success once the upsert has actually completed -- if the
    # DB write fails, the run should still look "stale" next time.
    _set_last_hash(dynamodb_client, hash_table, orders_source_key, orders_hash)
    _set_last_hash(dynamodb_client, hash_table, tonnage_source_key, tonnage_hash)

    summary = {
        "orders_total": len(order_rows),
        "orders_embedded": len(stale_orders),
        "orders_unchanged": len(order_rows) - len(stale_orders),
        "tonnage_total": len(tonnage_rows),
        "tonnage_embedded": len(stale_tonnage),
        "tonnage_unchanged": len(tonnage_rows) - len(stale_tonnage),
    }
    print(f"gold_loader summary: {summary}")
    return summary