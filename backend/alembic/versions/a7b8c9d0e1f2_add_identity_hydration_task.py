"""persist ordinary-user identity hydration progress

Revision ID: a7b8c9d0e1f2
Revises: f4a8c2e6b1d3
Create Date: 2026-09-01 18:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "f4a8c2e6b1d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "identity_hydration_task",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("completed", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("current_item", sa.String(length=255), nullable=True),
        sa.Column("overall", sa.String(length=32), nullable=True),
        sa.Column("detail", sa.String(length=256), nullable=True),
        sa.Column("results_json", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["account.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_identity_hydration_task_account_id",
        "identity_hydration_task",
        ["account_id"],
    )
    op.create_index(
        "ix_identity_hydration_task_status",
        "identity_hydration_task",
        ["status"],
    )
    op.create_index(
        "ix_identity_hydration_task_updated_at",
        "identity_hydration_task",
        ["updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_identity_hydration_task_updated_at",
        table_name="identity_hydration_task",
    )
    op.drop_index(
        "ix_identity_hydration_task_status",
        table_name="identity_hydration_task",
    )
    op.drop_index(
        "ix_identity_hydration_task_account_id",
        table_name="identity_hydration_task",
    )
    op.drop_table("identity_hydration_task")
