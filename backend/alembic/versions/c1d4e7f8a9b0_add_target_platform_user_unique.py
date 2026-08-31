"""add per-account unique platform user identity to contract targets

Revision ID: c1d4e7f8a9b0
Revises: b8c4d2e6f901
Create Date: 2026-09-01 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c1d4e7f8a9b0"
down_revision: Union[str, None] = "b8c4d2e6f901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("contract_target", schema=None) as batch_op:
        batch_op.create_unique_constraint(
            "uq_target_account_platform_user",
            ["account_id", "platform_user_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("contract_target", schema=None) as batch_op:
        batch_op.drop_constraint("uq_target_account_platform_user", type_="unique")
