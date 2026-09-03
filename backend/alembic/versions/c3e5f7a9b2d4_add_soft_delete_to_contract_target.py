"""add soft-delete flag to contract targets

Revision ID: c3e5f7a9b2d4
Revises: d9f3e5a7b1c2
Create Date: 2026-09-03 14:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c3e5f7a9b2d4"
down_revision: Union[str, None] = "d9f3e5a7b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 软删除：删除好友=打 deleted_at 标记而非物理删行，保留 SendJob 审计与身份唯一性。
    with op.batch_alter_table("contract_target", schema=None) as batch_op:
        batch_op.add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("contract_target", schema=None) as batch_op:
        batch_op.drop_column("deleted_at")
