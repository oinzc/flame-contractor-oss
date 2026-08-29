"""add douyin_nickname to account

Revision ID: f2c4d6e8a9b0
Revises: e5f2a8b9c3d1
Create Date: 2026-08-30 04:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'f2c4d6e8a9b0'
down_revision: Union[str, None] = 'e5f2a8b9c3d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('account', schema=None) as batch_op:
        batch_op.add_column(sa.Column('douyin_nickname', sqlmodel.sql.sqltypes.AutoString(length=80), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('account', schema=None) as batch_op:
        batch_op.drop_column('douyin_nickname')
