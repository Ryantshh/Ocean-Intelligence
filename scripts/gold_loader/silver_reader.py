"""Reads the published silver orders/tonnage NDJSON straight from S3.

Stdlib + boto3 only, mirroring scripts/glue_transform.py's write_dataset()
output layout: s3://{bucket}/{prefix}/orders/orders.json and
.../tonnage/tonnage.json, one JSON object per line.
"""

import json
from typing import Any

import boto3

from .logging_utils import get_logger

logger = get_logger("gold_loader")

# glue_transform.py's transform_tonnage() emits the field as "DWT" (see its
# select_or_null() alias list), but every downstream reader -- ai_platform's
# tables/tonnage.py Filters model, this loader's own tonnage_test.dwt column
# -- expects lowercase "dwt". Normalize on the way in.
TONNAGE_KEY_RENAMES = {"DWT": "dwt"}


def _read_ndjson_with_bytes(s3_client, bucket: str, key: str) -> tuple[bytes, list[dict[str, Any]]]:
    """Return the raw object bytes alongside the parsed rows.

    The raw bytes are returned so callers can hash the exact published
    content (see handler.py's file-level short-circuit) without re-fetching
    the object or hashing a re-serialized, potentially reordered, copy.
    """
    raw = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read()
    body = raw.decode("utf-8")
    rows = []
    for line in body.splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    logger.info("read %d row(s) from s3://%s/%s", len(rows), bucket, key)
    return raw, rows


def _object_key(prefix: str, table_dir: str, filename: str) -> str:
    base = prefix.strip("/")
    return f"{base}/{table_dir}/{filename}" if base else f"{table_dir}/{filename}"


def read_orders(bucket: str, prefix: str, s3_client=None) -> tuple[bytes, list[dict[str, Any]]]:
    s3_client = s3_client or boto3.client("s3")
    key = _object_key(prefix, "orders", "orders.json")
    return _read_ndjson_with_bytes(s3_client, bucket, key)


def read_tonnage(bucket: str, prefix: str, s3_client=None) -> tuple[bytes, list[dict[str, Any]]]:
    s3_client = s3_client or boto3.client("s3")
    key = _object_key(prefix, "tonnage", "tonnage.json")
    raw, rows = _read_ndjson_with_bytes(s3_client, bucket, key)
    for row in rows:
        for old_key, new_key in TONNAGE_KEY_RENAMES.items():
            if old_key in row:
                row[new_key] = row.pop(old_key)
    return raw, rows