"""add user table with admin seed

Revision ID: f0b1c2d3e4f5
Revises: a3c8e5d2f9b1
Create Date: 2026-09-04 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = "f0b1c2d3e4f5"
down_revision: Union[str, None] = "a3c8e5d2f9b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("username", sqlmodel.sql.sqltypes.AutoString(length=60), nullable=False),
        sa.Column("password_hash", sqlmodel.sql.sqltypes.AutoString(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )
    op.create_index("ix_user_username", "user", ["username"])


def downgrade() -> None:
    op.drop_index("ix_user_username", table_name="user")
    op.drop_table("user")
