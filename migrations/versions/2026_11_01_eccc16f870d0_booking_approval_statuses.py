"""booking approval statuses

Новые статусы записи: pending (ждёт подтверждения админом) и rejected
(отклонена админом). В SQLite нет ALTER CONSTRAINT, поэтому CHECK со списком
статусов меняется пересозданием таблицы bookings (batch mode). Миграции идут
с выключенными FK (см. migrations/env.py), иначе DROP старой таблицы
каскадно удалил бы booking_slots.

Revision ID: eccc16f870d0
Revises: ba48ad9173c0
Create Date: 2026-11-01 00:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'eccc16f870d0'
down_revision: Union[str, Sequence[str], None] = 'ba48ad9173c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _status_enum(*values: str) -> sa.Enum:
    return sa.Enum(
        *values, name="booking_status", native_enum=False, create_constraint=True, length=16
    )


OLD_STATUS = _status_enum("confirmed", "cancelled")
NEW_STATUS = _status_enum("pending", "confirmed", "cancelled", "rejected")

bookings = sa.table("bookings", sa.column("status", sa.String))


def upgrade() -> None:
    with op.batch_alter_table("bookings", recreate="always") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=OLD_STATUS,
            type_=NEW_STATUS,
            existing_nullable=False,
            existing_server_default="confirmed",
        )


def downgrade() -> None:
    # В старой схеме ожидающая запись считалась подтверждённой,
    # а отклонённая — отменённой.
    op.execute(bookings.update().where(bookings.c.status == "pending").values(status="confirmed"))
    op.execute(bookings.update().where(bookings.c.status == "rejected").values(status="cancelled"))
    with op.batch_alter_table("bookings", recreate="always") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=NEW_STATUS,
            type_=OLD_STATUS,
            existing_nullable=False,
            existing_server_default="confirmed",
        )
