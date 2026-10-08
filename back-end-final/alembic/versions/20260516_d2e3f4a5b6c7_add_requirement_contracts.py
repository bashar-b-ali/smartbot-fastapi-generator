"""add requirement contracts to project helpers

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-05-16 16:35:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "d2e3f4a5b6c7"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "project_helpers",
        sa.Column("requirement_contracts", sa.JSON(), nullable=True),
    )
    bind = op.get_bind()
    if bind.dialect.name == "mysql":
        op.execute(sa.text("UPDATE project_helpers SET requirement_contracts = JSON_ARRAY() WHERE requirement_contracts IS NULL"))
    else:
        op.execute(sa.text("UPDATE project_helpers SET requirement_contracts = '[]' WHERE requirement_contracts IS NULL"))
    op.alter_column("project_helpers", "requirement_contracts", nullable=False)


def downgrade() -> None:
    op.drop_column("project_helpers", "requirement_contracts")
