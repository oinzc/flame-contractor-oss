"""persist background chat scan progress

Revision ID: d2e6f8a0b1c3
Revises: c1d4e7f8a9b0
Create Date: 2026-09-01 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d2e6f8a0b1c3"
down_revision: Union[str, None] = "c1d4e7f8a9b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scan_task",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("total", sa.Integer(), nullable=True),
        sa.Column("completed", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("current_item", sa.String(length=255), nullable=True),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("max_rounds", sa.Integer(), nullable=False),
        sa.Column("overall", sa.String(length=32), nullable=True),
        sa.Column("detail", sa.String(length=256), nullable=True),
        sa.Column("entries_json", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["account.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scan_task_account_id", "scan_task", ["account_id"])
    op.create_index("ix_scan_task_status", "scan_task", ["status"])
    op.create_index("ix_scan_task_updated_at", "scan_task", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_scan_task_updated_at", table_name="scan_task")
    op.drop_index("ix_scan_task_status", table_name="scan_task")
    op.drop_index("ix_scan_task_account_id", table_name="scan_task")
    op.drop_table("scan_task")
