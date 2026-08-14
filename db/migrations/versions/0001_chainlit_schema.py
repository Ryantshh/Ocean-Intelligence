"""Chainlit SQLAlchemy data layer schema.

DDL copied verbatim from https://docs.chainlit.io/data-layers/sqlalchemy and
executed as raw SQL rather than rebuilt with ``op.create_table``. The data layer
addresses these columns as quoted camelCase identifiers, and a translation layer
is one more place for that to be silently lost.

Statements are unqualified. ``search_path`` is pinned to the target schema by
env.py, so they land in the environment being migrated.

Revision ID: 0001
Revises:
"""

from __future__ import annotations

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


CHAINLIT_TABLES: tuple[str, ...] = ("users", "threads", "steps", "elements", "feedbacks")


def upgrade() -> None:
    """Create the five Chainlit persistence tables and lock them down.

    Returns
    -------
    None
    """
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            "id" UUID PRIMARY KEY,
            "identifier" TEXT NOT NULL UNIQUE,
            "metadata" JSONB NOT NULL,
            "createdAt" TEXT
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS threads (
            "id" UUID PRIMARY KEY,
            "createdAt" TEXT,
            "name" TEXT,
            "userId" UUID,
            "userIdentifier" TEXT,
            "tags" TEXT[],
            "metadata" JSONB,
            FOREIGN KEY ("userId") REFERENCES users("id") ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS steps (
            "id" UUID PRIMARY KEY,
            "name" TEXT NOT NULL,
            "type" TEXT NOT NULL,
            "threadId" UUID NOT NULL,
            "parentId" UUID,
            "streaming" BOOLEAN NOT NULL,
            "waitForAnswer" BOOLEAN,
            "isError" BOOLEAN,
            "metadata" JSONB,
            "tags" TEXT[],
            "input" TEXT,
            "output" TEXT,
            "createdAt" TEXT,
            "command" TEXT,
            "start" TEXT,
            "end" TEXT,
            "generation" JSONB,
            "showInput" TEXT,
            "language" TEXT,
            "indent" INT,
            "defaultOpen" BOOLEAN,
            "modes" JSONB,
            FOREIGN KEY ("threadId") REFERENCES threads("id") ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS elements (
            "id" UUID PRIMARY KEY,
            "threadId" UUID,
            "type" TEXT,
            "url" TEXT,
            "chainlitKey" TEXT,
            "name" TEXT NOT NULL,
            "display" TEXT,
            "objectKey" TEXT,
            "size" TEXT,
            "page" INT,
            "language" TEXT,
            "forId" UUID,
            "mime" TEXT,
            "props" JSONB,
            FOREIGN KEY ("threadId") REFERENCES threads("id") ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS feedbacks (
            "id" UUID PRIMARY KEY,
            "forId" UUID NOT NULL,
            "threadId" UUID NOT NULL,
            "value" INT NOT NULL,
            "comment" TEXT,
            FOREIGN KEY ("threadId") REFERENCES threads("id") ON DELETE CASCADE
        )
        """
    )

    for table_name in CHAINLIT_TABLES:
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    """Drop the five tables, children before parents.

    Returns
    -------
    None
    """
    for table_name in reversed(CHAINLIT_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table_name} CASCADE")
