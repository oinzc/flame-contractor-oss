"""add auth fields and session table

Revision ID: a20a7d61a818
Revises: c7d8e9f0a1b2
Create Date: 2026-09-04 16:08:55.978046

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'a20a7d61a818'
down_revision: Union[str, None] = 'c7d8e9f0a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 服务端登录会话表：让 token 可撤销、可列出、可逐个登出。
    op.create_table(
        'auth_session',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.Column('user_agent', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
        sa.Column('ip_address', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_auth_session_user_id', 'auth_session', ['user_id'])

    # 平台账号(User)资料字段：display_name/email/is_superuser。
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('display_name', sqlmodel.sql.sqltypes.AutoString(length=60), nullable=True))
        batch_op.add_column(sa.Column('email', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True))
        batch_op.add_column(sa.Column('is_superuser', sa.Boolean(), nullable=False, server_default=sa.text('0')))
        batch_op.create_index('ix_user_email', ['email'])
        batch_op.create_index('ix_user_is_superuser', ['is_superuser'])


def downgrade() -> None:
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_index('ix_user_is_superuser')
        batch_op.drop_index('ix_user_email')
        batch_op.drop_column('is_superuser')
        batch_op.drop_column('email')
        batch_op.drop_column('display_name')

    op.drop_index('ix_auth_session_user_id', table_name='auth_session')
    op.drop_table('auth_session')
