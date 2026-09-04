"""add target send_time_hhmm

Revision ID: a3c8e5d2f9b1
Revises: ec4a2b1d8f06
Create Date: 2026-09-03 16:01:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = "a3c8e5d2f9b1"
down_revision: Union[str, None] = "ec4a2b1d8f06"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 好友级精确发送时刻（HH:MM，账号本地时间）；为空=跟随账号（账号精确时刻或窗口随机）。
    with op.batch_alter_table("contract_target", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "send_time_hhmm",
                sqlmodel.sql.sqltypes.AutoString(length=5),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("contract_target", schema=None) as batch_op:
        batch_op.drop_column("send_time_hhmm")
