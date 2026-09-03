"""allow send_job.template_id to be null for manual raw-text sends

Revision ID: d9f3e5a7b1c2
Revises: c6e7f8a9b0d1
Create Date: 2026-09-03 11:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d9f3e5a7b1c2"
down_revision: Union[str, None] = "c6e7f8a9b0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 单好友手动发送「直接输入文本」时 SendJob 不引用模板，故 template_id 允许为空。
    with op.batch_alter_table("send_job", schema=None) as batch_op:
        batch_op.alter_column(
            "template_id",
            existing_type=sa.String(),
            existing_nullable=False,
            nullable=True,
        )


def downgrade() -> None:
    # 回退时只接受仍非空的列；若有空值历史记录，需人工处理后再回退。
    with op.batch_alter_table("send_job", schema=None) as batch_op:
        batch_op.alter_column(
            "template_id",
            existing_type=sa.String(),
            existing_nullable=True,
            nullable=False,
        )
