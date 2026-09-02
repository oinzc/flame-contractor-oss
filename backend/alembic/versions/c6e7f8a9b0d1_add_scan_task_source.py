"""mark whether a persisted scan was manual or automatic

Revision ID: c6e7f8a9b0d1
Revises: a7b8c9d0e1f2
Create Date: 2026-09-02 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c6e7f8a9b0d1"
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 既有快照都来自好友页主动扫描，迁移时安全归类为 manual。
    with op.batch_alter_table("scan_task", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "source",
                sa.String(length=16),
                nullable=False,
                server_default=sa.text("'manual'"),
            )
        )
        batch_op.create_index("ix_scan_task_source", ["source"])


def downgrade() -> None:
    with op.batch_alter_table("scan_task", schema=None) as batch_op:
        batch_op.drop_index("ix_scan_task_source")
        batch_op.drop_column("source")
