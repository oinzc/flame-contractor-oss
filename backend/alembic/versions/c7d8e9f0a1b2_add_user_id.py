"""add user_id to account and message_template

Revision ID: c7d8e9f0a1b2
Revises: f0b1c2d3e4f5
Create Date: 2026-09-04 20:01:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, None] = "f0b1c2d3e4f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("account", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "user_id",
                sqlmodel.sql.sqltypes.AutoString(),
                nullable=True,
            )
        )
        batch_op.create_foreign_key("fk_account_user", "user", ["user_id"], ["id"], ondelete="RESTRICT")
        batch_op.create_index("ix_account_user_id", ["user_id"])
        # 唯一性改为「平台账号内 display_name 唯一」：去掉旧的全局唯一索引
        batch_op.drop_index("ix_account_display_name")
        batch_op.create_unique_constraint("uq_account_user_display_name", ["user_id", "display_name"])
    # 模板同理：唯一性改为 user 内 name 唯一
    with op.batch_alter_table("message_template", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "user_id",
                sqlmodel.sql.sqltypes.AutoString(),
                nullable=True,
            )
        )
        batch_op.create_foreign_key("fk_template_user", "user", ["user_id"], ["id"], ondelete="RESTRICT")
        batch_op.create_index("ix_message_template_user_id", ["user_id"])
        batch_op.drop_index("ix_message_template_name")
        batch_op.create_unique_constraint("uq_template_user_name", ["user_id", "name"])


def downgrade() -> None:
    with op.batch_alter_table("message_template", schema=None) as batch_op:
        batch_op.drop_constraint("uq_template_user_name", type_="unique")
        batch_op.create_index("ix_message_template_name", ["name"], unique=True)
        batch_op.drop_index("ix_message_template_user_id")
        batch_op.drop_constraint("fk_template_user", type_="foreignkey")
        batch_op.drop_column("user_id")
    with op.batch_alter_table("account", schema=None) as batch_op:
        batch_op.drop_constraint("uq_account_user_display_name", type_="unique")
        batch_op.create_index("ix_account_display_name", ["display_name"], unique=True)
        batch_op.drop_index("ix_account_user_id")
        batch_op.drop_constraint("fk_account_user", type_="foreignkey")
        batch_op.drop_column("user_id")
