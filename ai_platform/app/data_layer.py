"""Chainlit persistence against Supabase Postgres.

Environments share one database and are separated by schema. ``search_path`` is
pinned per connection because Chainlit addresses its five tables as unqualified
literals, so the schema is what decides which environment's history is read.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re

import chainlit as cl
from botocore.config import Config
from chainlit.data.base import BaseDataLayer
from chainlit.data.sql_alchemy import SQLAlchemyDataLayer
from chainlit.data.storage_clients.base import BaseStorageClient
from chainlit.data.storage_clients.s3 import S3StorageClient
from chainlit.types import (
    PageInfo,
    PaginatedResponse,
    Pagination,
    ThreadDict,
    ThreadFilter,
)
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

load_dotenv()

SCHEMA_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_logger = logging.getLogger(__name__)
_background_deletes: set[asyncio.Task[None]] = set()

THREAD_LIST_QUERY = """
    SELECT
        t."id" AS thread_id,
        t."createdAt" AS thread_createdat,
        t."name" AS thread_name,
        t."userId" AS user_id,
        t."userIdentifier" AS user_identifier,
        t."tags" AS thread_tags,
        t."metadata" AS thread_metadata,
        MAX(s."createdAt") AS updatedat
    FROM threads t
    LEFT JOIN steps s ON t."id" = s."threadId"
    WHERE t."userId" = :user_id
    GROUP BY t."id", t."createdAt", t."name", t."userId", t."userIdentifier", t."tags", t."metadata"
    ORDER BY updatedat DESC NULLS LAST
    LIMIT :limit
"""
"""Thread rows for the sidebar, newest activity first, without steps or elements."""


class ThreadListDataLayer(SQLAlchemyDataLayer):
    """SQLAlchemy layer whose thread list skips steps and elements.

    The stock ``list_threads`` loads every step and element of every thread to
    build the sidebar, which the sidebar never reads. Only the thread rows are
    fetched here; a thread's steps and elements load when it is opened.

    ``delete_element`` returns before the row is gone. Chainlit removes a form
    from the screen only after the delete completes, so awaiting it leaves an
    answered form on screen for the length of two database round trips.
    """

    async def delete_element(self, element_id: str, thread_id: str | None = None) -> None:
        """Schedule the element delete and return without waiting for it.

        Parameters
        ----------
        element_id : str
            Element to delete.
        thread_id : str or None
            Thread the element belongs to, passed through unchanged.

        Returns
        -------
        None
        """
        task = asyncio.create_task(self._delete_element_later(element_id, thread_id))
        _background_deletes.add(task)
        task.add_done_callback(_background_deletes.discard)

    async def _delete_element_later(self, element_id: str, thread_id: str | None) -> None:
        """Run the stock delete, logging a failure instead of raising it.

        Parameters
        ----------
        element_id : str
            Element to delete.
        thread_id : str or None
            Thread the element belongs to.

        Returns
        -------
        None
        """
        try:
            await super().delete_element(element_id, thread_id)
        except Exception:
            _logger.exception("background delete of element %s failed", element_id)

    async def list_threads(
        self, pagination: Pagination, filters: ThreadFilter
    ) -> PaginatedResponse:
        """Page through the user's threads by id cursor.

        Parameters
        ----------
        pagination : Pagination
            Page size and the id of the last thread on the previous page.
        filters : ThreadFilter
            ``userId`` is required. ``search`` matches the thread name. The
            feedback filter is not applied, since it needs step data.

        Returns
        -------
        PaginatedResponse
            One page of threads with empty ``steps`` and ``elements``.

        Raises
        ------
        ValueError
            If ``filters.userId`` is unset.
        """
        if not filters.userId:
            raise ValueError("userId is required")
        rows = await self.execute_sql(
            query=THREAD_LIST_QUERY,
            parameters={"user_id": filters.userId, "limit": self.user_thread_limit},
        )
        threads: list[ThreadDict] = [
            ThreadDict(
                id=str(row["thread_id"]),
                createdAt=row["thread_createdat"],
                name=row["thread_name"],
                userId=row["user_id"],
                userIdentifier=row["user_identifier"],
                tags=row["thread_tags"],
                metadata=row["thread_metadata"],
                steps=[],
                elements=[],
            )
            for row in (rows if isinstance(rows, list) else [])
        ]
        search_keyword = filters.search.lower() if filters.search else None
        if search_keyword:
            threads = [
                thread for thread in threads if search_keyword in (thread["name"] or "").lower()
            ]

        start = 0
        if pagination.cursor:
            for index, thread in enumerate(threads):
                if thread["id"] == pagination.cursor:
                    start = index + 1
                    break
        end = start + pagination.first
        page = threads[start:end]
        return PaginatedResponse(
            pageInfo=PageInfo(
                hasNextPage=len(threads) > end,
                startCursor=page[0]["id"] if page else None,
                endCursor=page[-1]["id"] if page else None,
            ),
            data=page,
        )


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

    data_layer = ThreadListDataLayer(
        conninfo=conninfo,
        connect_args={"server_settings": {"search_path": get_schema_name()}},
        storage_provider=get_storage_client(),
    )

    # SQLAlchemyDataLayer.__init__ builds its engine with create_async_engine's
    # bare defaults -- pool_size=5, max_overflow=10, i.e. up to 15 real
    # connections for chat persistence alone, which is Supabase's *entire*
    # project-wide session-pooler cap (see ai_platform/backend/db.py's pool
    # comment). Its constructor doesn't expose pool sizing, so the engine it
    # just built (never having opened a connection yet -- engines are lazy)
    # is swapped for a tighter one here instead. Confirmed live that using
    # chat and the dashboard at once could otherwise exhaust the cap with
    # "EMAXCONNSESSION ... max clients are limited to pool_size: 15".
    data_layer.engine = create_async_engine(
        conninfo,
        connect_args={"server_settings": {"search_path": get_schema_name()}},
        pool_size=3,
        max_overflow=2,
    )
    data_layer.async_session = sessionmaker(
        bind=data_layer.engine, expire_on_commit=False, class_=AsyncSession
    )
    return data_layer
