"""Chainlit persistence against Supabase Postgres.

Environments share one database and are separated by schema. ``search_path`` is
pinned per connection because Chainlit addresses its five tables as unqualified
literals, so the schema is what decides which environment's history is read.
"""

from __future__ import annotations

import os
import re

import chainlit as cl
from botocore.config import Config
from chainlit.data.base import BaseDataLayer
from chainlit.data.sql_alchemy import SQLAlchemyDataLayer
from chainlit.data.storage_clients.base import BaseStorageClient
from chainlit.data.storage_clients.s3 import S3StorageClient
from dotenv import load_dotenv

load_dotenv()

SCHEMA_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def get_schema_name() -> str:
    """Read the environment schema this process should read and write.

    Returns
    -------
    str
        Schema name, defaulting to ``dev``.

    Raises
    ------
    RuntimeError
        If the name is not a bare identifier.
    """
    schema = os.environ.get("CHAINLIT_DB_SCHEMA", "dev").strip()
    if not SCHEMA_NAME_PATTERN.match(schema):
        raise RuntimeError(f"CHAINLIT_DB_SCHEMA is not a valid identifier: {schema!r}")
    return schema


def get_storage_client() -> BaseStorageClient | None:
    """Build the Supabase Storage client, when it has been configured.

    Storage is only required for elements. With it unset the data layer starts
    and persists threads, steps and users, logging a warning and failing only on
    ``create_element``.

    The ``CHAINLIT_S3_`` prefix keeps these distinct from the AWS credentials and
    data-lake bucket names that share the same ``.env``.

    ``signature_version`` must be set explicitly. Supabase's S3 gateway accepts
    only SigV4, and botocore presigns GET URLs with SigV2 unless told otherwise —
    even when the client config already reports ``s3v4``, because query-auth
    signing is chosen separately from request signing. Left unset, uploads
    succeed and every read of a persisted element returns 403, so the failure
    only appears when a thread is reloaded.

    Returns
    -------
    BaseStorageClient or None
        Configured client, or None when the S3 credentials are absent.
    """
    bucket = os.environ.get("CHAINLIT_S3_BUCKET", "").strip()
    access_key = os.environ.get("CHAINLIT_S3_ACCESS_KEY_ID", "").strip()
    secret_key = os.environ.get("CHAINLIT_S3_SECRET_ACCESS_KEY", "").strip()
    endpoint_url = os.environ.get("CHAINLIT_S3_ENDPOINT_URL", "").strip()

    if not all((bucket, access_key, secret_key, endpoint_url)):
        return None

    return S3StorageClient(
        bucket=bucket,
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=os.environ.get("CHAINLIT_S3_REGION", "us-east-1").strip(),
        config=Config(signature_version="s3v4"),
    )


@cl.data_layer
def get_data_layer() -> BaseDataLayer:
    """Return the persistence layer Chainlit should use.

    Returns
    -------
    BaseDataLayer
        SQLAlchemy layer bound to this environment's schema.

    Raises
    ------
    RuntimeError
        If ``CHAINLIT_DATABASE_URL`` is unset or not an asyncpg URL.
    """
    conninfo = os.environ.get("CHAINLIT_DATABASE_URL", "").strip()
    if not conninfo:
        raise RuntimeError("CHAINLIT_DATABASE_URL is not set. Check .env.")
    if "+asyncpg" not in conninfo:
        raise RuntimeError(
            "CHAINLIT_DATABASE_URL must use the postgresql+asyncpg:// driver."
        )

    return SQLAlchemyDataLayer(
        conninfo=conninfo,
        connect_args={"server_settings": {"search_path": get_schema_name()}},
        storage_provider=get_storage_client(),
    )
