"""add image_path to message_template

Revision ID: a0737c65f237
Revises: 15a5feca9ee1
Create Date: 2026-08-28 09:45:01.668434

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'a0737c65f237'
down_revision: Union[str, None] = '15a5feca9ee1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite 的 Enum 本质是文本列，TEXT/IMAGE 只是取值集合不同，无需改存储结构；
    # 仅新增 image_path 列。type 列保持原 VARCHAR 存储，新增 'IMAGE' 只是应用层取值。
    with op.batch_alter_table('message_template', schema=None) as batch_op:
        batch_op.add_column(sa.Column('image_path', sqlmodel.sql.sqltypes.AutoString(length=500), nullable=True))

    # ### end Alembic commands ###


def downgrade() -> None:
    with op.batch_alter_table('message_template', schema=None) as batch_op:
        batch_op.drop_column('image_path')

    # ### end Alembic commands ###
