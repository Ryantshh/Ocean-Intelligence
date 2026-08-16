"""Tracks the last-processed content hash of each silver file, so the gold
loader can skip a full run when Glue republished a file with byte-identical
content (e.g. a run that only touched the other dataset).

Uses a small DynamoDB table (one item per silver file) rather than Supabase,
so this check can short-circuit BEFORE opening the Supabase connection.
"""

import hashlib

import boto3


def content_hash(raw_bytes: bytes) -> str:
    return hashlib.sha256(raw_bytes).hexdigest()


def get_last_hash(table_name: str, source_key: str, dynamodb_client=None) -> str | None:
    dynamodb_client = dynamodb_client or boto3.client("dynamodb")
    response = dynamodb_client.get_item(
        TableName=table_name,
        Key={"source_key": {"S": source_key}},
        ConsistentRead=True,
    )
    item = response.get("Item")
    return item["content_hash"]["S"] if item else None


def set_last_hash(table_name: str, source_key: str, content_hash_value: str, dynamodb_client=None) -> None:
    dynamodb_client = dynamodb_client or boto3.client("dynamodb")
    dynamodb_client.put_item(
        TableName=table_name,
        Item={
            "source_key": {"S": source_key},
            "content_hash": {"S": content_hash_value},
        },
    )