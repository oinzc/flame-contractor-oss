"""add failure_streak to account

Revision ID: c8d21f4a9e57
Revises: fe2a91c8b3d4
Create Date: 2026-08-29 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8d21f4a9e57'
down_revision: Union[str, None] = 'fe2a91c8b3d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('account', schema=None) as batch_op:
        batch_op.add_column(sa.Column('failure_streak', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    with op.batch_alter_table('account', schema=None) as batch_op:
        batch_op.drop_column('failure_streak')
