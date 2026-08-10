"""Alembic environment for the Chainlit persistence schema.

Environments are separated by Postgres schema rather than by database or table
prefix. The target schema comes from ``CHAINLIT_DB_SCHEMA`` and is applied two
ways: ``search_path`` on the connection, so unqualified DDL lands in the right
place, and ``version_table_schema``, so each environment tracks its own
revision independently.
"""

from __future__ import annotations

import asyncio
import os
import re
from logging.config import fileConfig

from alembic import context
from alembic.runtime.environment import NameFilterParentNames, NameFilterType
from dotenv import load_dotenv
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

load_dotenv()

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None

SCHEMA_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def get_database_url() -> str:
    """Read the migration connection string from the environment.

    Returns
    -------
    str
        A SQLAlchemy URL using the asyncpg driver.

    Raises
    ------
    RuntimeError
        If ``CHAINLIT_DATABASE_URL`` is unset or does not use asyncpg.
    """
    url = os.environ.get("CHAINLIT_DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("CHAINLIT_DATABASE_URL is not set. Check .env.")
    if "+asyncpg" not in url:
        raise RuntimeError(
            "CHAINLIT_DATABASE_URL must use the postgresql+asyncpg:// driver."
        )
    return url


def get_schema_name() -> str:
    """Read the target schema for this migration run.

    Returns
    -------
    str
        Schema name, defaulting to ``dev``.

    Raises
    ------
    RuntimeError
        If the name is not a bare identifier. The value is interpolated into
        DDL, so anything else is rejected rather than quoted.
    """
    schema = os.environ.get("CHAINLIT_DB_SCHEMA", "dev").strip()
    if not SCHEMA_NAME_PATTERN.match(schema):
        raise RuntimeError(f"CHAINLIT_DB_SCHEMA is not a valid identifier: {schema!r}")
    return schema


def include_name(
    name: str | None,
    type_: NameFilterType,
    parent_names: NameFilterParentNames,
) -> bool:
    """Restrict autogenerate to tables this project defines.

    Chainlit owns the five persistence tables and its DDL is not mirrored in
    ``target_metadata``. Without this filter autogenerate treats them as
    extraneous and proposes dropping them, which would destroy chat history.

    Parameters
    ----------
    name : str or None
        Name of the reflected object.
    type_ : NameFilterType
        Object kind, such as ``table`` or ``schema``.
    parent_names : NameFilterParentNames
        Owning object names, supplied by Alembic. Unused, but part of the
        callback signature.

    Returns
    -------
    bool
        True when the object may take part in autogenerate.
    """
    if type_ != "table":
        return True
    if target_metadata is None:
        return False
    return name in target_metadata.tables


def run_migrations_offline() -> None:
    """Emit migration SQL to stdout without connecting to a database.

    Returns
    -------
    None
    """
    schema = get_schema_name()
    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_name=include_name,
        include_schemas=False,
        version_table_schema=schema,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Create the target schema if needed, then run pending revisions.

    The schema creation is committed before ``context.configure``. Left in an
    open transaction it makes Alembic treat the transaction as externally owned,
    so Alembic never commits and the migration silently rolls back while still
    logging success.

    Parameters
    ----------
    connection : Connection
        Open synchronous-facing connection supplied by ``run_sync``.

    Returns
    -------
    None
    """
    schema = get_schema_name()
    connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    connection.commit()

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_name=include_name,
        include_schemas=False,
        version_table_schema=schema,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Open an asyncpg engine pinned to the target schema and migrate.

    Returns
    -------
    None
    """
    schema = get_schema_name()
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_database_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={"server_settings": {"search_path": schema}},
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point for online migrations.

    Returns
    -------
    None
    """
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
