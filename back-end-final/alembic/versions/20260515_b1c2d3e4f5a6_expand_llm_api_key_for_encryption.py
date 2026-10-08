"""expand llm api key for encryption

Revision ID: b1c2d3e4f5a6
Revises: 9c8d7e6f5a4b
Create Date: 2026-05-15 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: str | None = "9c8d7e6f5a4b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "llm_models",
        "api_key",
        existing_type=sa.String(length=500),
        type_=sa.String(length=2000),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "llm_models",
        "api_key",
        existing_type=sa.String(length=2000),
        type_=sa.String(length=500),
        existing_nullable=False,
    )
