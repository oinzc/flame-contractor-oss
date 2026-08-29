"""add template_pool_item (M8-1 文案池)

Revision ID: e5f2a8b9c3d1
Revises: c8d21f4a9e57
Create Date: 2026-08-29 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'e5f2a8b9c3d1'
down_revision: Union[str, None] = 'c8d21f4a9e57'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'template_pool_item',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('template_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('content', sqlmodel.sql.sqltypes.AutoString(length=500), nullable=False),
        sa.Column('weekday_mask', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['template_id'], ['message_template.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_template_pool_item_template_id'), 'template_pool_item', ['template_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_template_pool_item_template_id'), table_name='template_pool_item')
    op.drop_table('template_pool_item')
