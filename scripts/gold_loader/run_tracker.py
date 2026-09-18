"""Tracks per-row content hashes in DynamoDB (``GOLD_PROCESSED_HASH_TABLE``)
so the gold loader can tell what changed without ever running a SELECT
against Supabase.

Keys are ``order#<order_id>`` / ``tonnage#<tonnage_row_key>``. glue_transform.py
republishes the whole silver dataset every run, not a delta, so without this
most rows here are identical to what gold already has -- instead of asking
Postgres what's already there, the loader asks DynamoDB whether this row's
hash already matches what was last written, and only ever sends Postgres the
rows that actually need writing.

Trade-off, worth knowing: this trusts DynamoDB's record of "what gold
already has" rather than gold's live content. It only drifts from reality if
a row was written into Postgres by something other than this loader, or a
run wrote to Postgres but crashed before recording the new hash here -- the
latter is harmless (that row is simply treated as changed again next run,
a redundant write rather than a missed one).
"""

import hashlib

ROW_HASH_ATTR = "row_hash"
EMBEDDING_HASH_ATTR = "embedding_source_hash"

# DynamoDB's hard per-call limits for the batch APIs.
_BATCH_GET_SIZE = 100
_BATCH_WRITE_SIZE = 25


def row_hash(row: dict, fields: tuple[str, ...]) -> str:
    """SHA-256 over a row's given field values, in field order.

    Same shape as embeddings.source_hash(), just over the full set of core
    columns rather than only the embeddable ones -- this is what tells the
    caller "did anything about this row change", where source_hash() narrows
    that down to "did the row's embeddable text specifically change".
    """
    joined = "||".join(str(row.get(field) or "") for field in fields)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def get_row_hashes(dynamodb_client, table_name: str, row_keys: list[str]) -> dict[str, dict[str, str]]:
    """Batch-fetch {row_key: {"row_hash": ..., "embedding_source_hash": ...}}.

    A key with no stored item (a brand-new row) is simply absent from the
    result -- callers treat a missing entry the same as a changed one.
    """
    found: dict[str, dict[str, str]] = {}
    unique_keys = list(dict.fromkeys(row_keys))

    for start in range(0, len(unique_keys), _BATCH_GET_SIZE):
        batch = unique_keys[start : start + _BATCH_GET_SIZE]
        request_items = {
            table_name: {
                "Keys": [{"source_key": {"S": key}} for key in batch],
                "ConsistentRead": True,
            }
        }
        # BatchGetItem can return UnprocessedKeys under throttling; keep
        # retrying just those until DynamoDB has served every key in the batch.
        while request_items:
            response = dynamodb_client.batch_get_item(RequestItems=request_items)
            for item in response.get("Responses", {}).get(table_name, []):
                key = item["source_key"]["S"]
                found[key] = {
                    ROW_HASH_ATTR: item.get(ROW_HASH_ATTR, {}).get("S", ""),
                    EMBEDDING_HASH_ATTR: item.get(EMBEDDING_HASH_ATTR, {}).get("S", ""),
                }
            request_items = response.get("UnprocessedKeys") or {}

    return found


def put_row_hashes(dynamodb_client, table_name: str, items: dict[str, dict[str, str]]) -> None:
    """Batch-write {row_key: {"row_hash": ..., "embedding_source_hash": ...}}."""
    entries = list(items.items())

    for start in range(0, len(entries), _BATCH_WRITE_SIZE):
        batch = entries[start : start + _BATCH_WRITE_SIZE]
        request_items = {
            table_name: [
                {
                    "PutRequest": {
                        "Item": {
                            "source_key": {"S": key},
                            ROW_HASH_ATTR: {"S": hashes[ROW_HASH_ATTR]},
                            EMBEDDING_HASH_ATTR: {"S": hashes[EMBEDDING_HASH_ATTR]},
                        }
                    }
                }
                for key, hashes in batch
            ]
        }
        # BatchWriteItem can return UnprocessedItems under throttling; keep
        # retrying just those until every item in the batch is written.
        while request_items:
            response = dynamodb_client.batch_write_item(RequestItems=request_items)
            request_items = response.get("UnprocessedItems") or {}
