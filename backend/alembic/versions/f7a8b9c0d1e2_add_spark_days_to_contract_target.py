"""add official spark days to contract targets

Revision ID: f7a8b9c0d1e2
Revises: a20a7d61a818
Create Date: 2026-09-11 14:05:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, None] = "a20a7d61a818"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("contract_target", schema=None) as batch_op:
        batch_op.add_column(sa.Column("spark_days", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("contract_target", schema=None) as batch_op:
        batch_op.drop_column("spark_days")
