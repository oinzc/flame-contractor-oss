"""add account send_time_hhmm

Revision ID: ec4a2b1d8f06
Revises: c3e5f7a9b2d4
Create Date: 2026-09-03 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = "ec4a2b1d8f06"
down_revision: Union[str, None] = "c3e5f7a9b2d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 账号级精确发送时刻（HH:MM）；为空=在发送窗口内随机取时，非空=固定时刻发送。
    with op.batch_alter_table("account", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "send_time_hhmm",
                sqlmodel.sql.sqltypes.AutoString(length=5),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("account", schema=None) as batch_op:
        batch_op.drop_column("send_time_hhmm")
