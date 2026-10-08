"""add user-owned llm models

Revision ID: 7b9c1d2e3f4a
Revises: a1b2c3d4e5f6
Create Date: 2026-05-15 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

import app.db.mixins
from alembic import op

revision: str = "7b9c1d2e3f4a"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("name", "llm_models", type_="unique")
    op.add_column(
        "llm_models",
        sa.Column("user_id", app.db.mixins.UUIDChar(length=36), nullable=True),
    )
    op.create_index(op.f("ix_llm_models_user_id"), "llm_models", ["user_id"], unique=False)
    op.create_foreign_key(
        "fk_llm_models_user_id_users",
        "llm_models",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_llm_models_user_id_name",
        "llm_models",
        ["user_id", "name"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_llm_models_user_id_name", "llm_models", type_="unique")
    op.drop_constraint("fk_llm_models_user_id_users", "llm_models", type_="foreignkey")
    op.drop_index(op.f("ix_llm_models_user_id"), table_name="llm_models")
    op.drop_column("llm_models", "user_id")
    op.create_unique_constraint("name", "llm_models", ["name"])
