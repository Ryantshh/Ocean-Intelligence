"""Add the step columns Chainlit 2.11.1 writes but the published DDL omits.

``create_step`` inserts every non-None key of ``StepDict``, so a field the
installed version sets and the table lacks is an ``UndefinedColumnError`` rather
than a silently dropped value. ``autoCollapse`` and ``icon`` are both in
``StepDict`` and both absent from the schema in the docs this project copied.

Nothing surfaced until the agent started emitting ``cl.Step`` progress steps —
plain messages set neither field, so the gap sat dormant.

``feedback`` is also in ``StepDict`` and is deliberately not added: it belongs to
the ``feedbacks`` table and is never written to ``steps``.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add the missing step columns.

    Returns
    -------
    None
    """
    op.execute('ALTER TABLE steps ADD COLUMN IF NOT EXISTS "autoCollapse" BOOLEAN')
    op.execute('ALTER TABLE steps ADD COLUMN IF NOT EXISTS "icon" TEXT')


def downgrade() -> None:
    """Drop the columns added by this revision.

    Returns
    -------
    None
    """
    op.execute('ALTER TABLE steps DROP COLUMN IF EXISTS "icon"')
    op.execute('ALTER TABLE steps DROP COLUMN IF EXISTS "autoCollapse"')
