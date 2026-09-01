"""add stable conversation identity to contract targets

Revision ID: f4a8c2e6b1d3
Revises: e7f9a1c3d5b7
Create Date: 2026-09-01 11:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "f4a8c2e6b1d3"
down_revision: Union[str, None] = "e7f9a1c3d5b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("contract_target", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "platform_conversation_id",
                sqlmodel.sql.sqltypes.AutoString(length=128),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "conversation_type",
                sqlmodel.sql.sqltypes.AutoString(length=16),
                nullable=True,
            )
        )
        batch_op.create_index(
            "ix_contract_target_platform_conversation_id",
            ["platform_conversation_id"],
            unique=False,
        )
        batch_op.create_unique_constraint(
            "uq_target_account_platform_conversation",
            ["account_id", "platform_conversation_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("contract_target", schema=None) as batch_op:
        batch_op.drop_constraint(
            "uq_target_account_platform_conversation", type_="unique"
        )
        batch_op.drop_index("ix_contract_target_platform_conversation_id")
        batch_op.drop_column("conversation_type")
        batch_op.drop_column("platform_conversation_id")
