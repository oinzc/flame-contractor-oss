"""persist reconciliation snapshots for completed chat scans

Revision ID: f8a9b0c1d2e3
Revises: f7a8b9c0d1e2
Create Date: 2026-09-11 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f8a9b0c1d2e3"
down_revision: Union[str, None] = "f7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


EMPTY_RECONCILIATION = (
    "{\"counts\":{\"changed\":0,\"no_spark\":0,\"new\":0,"
    "\"not_observed\":0,\"unchanged\":0},\"entries\":[]}"
)


def upgrade() -> None:
    with op.batch_alter_table("scan_task", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "reconciliation_json",
                sa.Text(),
                nullable=False,
                server_default=sa.text(f"'{EMPTY_RECONCILIATION}'"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("scan_task", schema=None) as batch_op:
        batch_op.drop_column("reconciliation_json")

