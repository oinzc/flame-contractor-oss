"""add sticker fields to message_template

Revision ID: fe2a91c8b3d4
Revises: a0737c65f237
Create Date: 2026-08-28 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'fe2a91c8b3d4'
down_revision: Union[str, None] = 'a0737c65f237'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # type 列保持原 VARCHAR 存储，新增 'sticker' 只是应用层取值，无需改存储。
    with op.batch_alter_table('message_template', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sticker_name', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True))
        batch_op.add_column(sa.Column('sticker_label', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True))
        batch_op.add_column(sa.Column('sticker_category', sqlmodel.sql.sqltypes.AutoString(length=32), nullable=True))
        batch_op.add_column(sa.Column('sticker_fallback_index', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('message_template', schema=None) as batch_op:
        batch_op.drop_column('sticker_fallback_index')
        batch_op.drop_column('sticker_category')
        batch_op.drop_column('sticker_label')
        batch_op.drop_column('sticker_name')
