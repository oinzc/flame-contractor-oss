"""add official spark snapshot to contract targets

Revision ID: b8c4d2e6f901
Revises: b7e3d1f9a2c4
Create Date: 2026-08-31 16:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = "b8c4d2e6f901"
down_revision: Union[str, None] = "b7e3d1f9a2c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("contract_target", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("spark_kind", sqlmodel.sql.sqltypes.AutoString(length=16), nullable=True)
        )
        batch_op.add_column(
            sa.Column("spark_label", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True)
        )
        batch_op.add_column(sa.Column("spark_scanned_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("contract_target", schema=None) as batch_op:
        batch_op.drop_column("spark_scanned_at")
        batch_op.drop_column("spark_label")
        batch_op.drop_column("spark_kind")
