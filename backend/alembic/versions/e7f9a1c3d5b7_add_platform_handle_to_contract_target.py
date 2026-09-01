"""add readable platform handle to contract targets

Revision ID: e7f9a1c3d5b7
Revises: d2e6f8a0b1c3
Create Date: 2026-09-01 10:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "e7f9a1c3d5b7"
down_revision: Union[str, None] = "d2e6f8a0b1c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("contract_target", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "platform_handle",
                sqlmodel.sql.sqltypes.AutoString(length=128),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("contract_target", schema=None) as batch_op:
        batch_op.drop_column("platform_handle")
