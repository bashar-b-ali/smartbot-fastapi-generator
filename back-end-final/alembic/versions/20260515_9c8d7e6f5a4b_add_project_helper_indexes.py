"""add project helper indexes

Revision ID: 9c8d7e6f5a4b
Revises: 7b9c1d2e3f4a
Create Date: 2026-05-15 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "9c8d7e6f5a4b"
down_revision: str | None = "7b9c1d2e3f4a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "project_helpers",
        sa.Column("database_schema", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "project_helpers",
        sa.Column("api_routes", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    op.add_column(
        "project_helpers",
        sa.Column("file_index", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )


def downgrade() -> None:
    op.drop_column("project_helpers", "file_index")
    op.drop_column("project_helpers", "api_routes")
    op.drop_column("project_helpers", "database_schema")
