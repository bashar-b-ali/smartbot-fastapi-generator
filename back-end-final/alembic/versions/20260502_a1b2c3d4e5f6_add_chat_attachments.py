"""add chat message attachments + ensure column for older deployments

Revision ID: a1b2c3d4e5f6
Revises: 8af40a20e3f4
Create Date: 2026-05-02 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'a1b2c3d4e5f6'
down_revision: str | None = '8af40a20e3f4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'chat_messages',
        sa.Column('attachments', sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('chat_messages', 'attachments')
