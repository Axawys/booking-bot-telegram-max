"""generic demo services

Демо-услуги салона заменяются обезличенными «Услуга за N ₽», чтобы демо
подходило любому бизнесу. Старые услуги, на которые уже есть записи, не
удаляются (FK RESTRICT и история записей), а отключаются.

Revision ID: ba48ad9173c0
Revises: b51444843fb3
Create Date: 2026-11-01 00:20:39.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ba48ad9173c0'
down_revision: Union[str, Sequence[str], None] = 'b51444843fb3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


services = sa.table(
    "services",
    sa.column("id", sa.Integer),
    sa.column("name", sa.String),
    sa.column("price", sa.Integer),
    sa.column("duration_minutes", sa.Integer),
    sa.column("is_active", sa.Boolean),
    sa.column("sort_order", sa.Integer),
)
bookings = sa.table("bookings", sa.column("service_id", sa.Integer))

OLD_SERVICES = [
    {"name": "Женская стрижка", "price": 1500, "duration_minutes": 60, "sort_order": 10},
    {"name": "Мужская стрижка", "price": 1000, "duration_minutes": 30, "sort_order": 20},
    {"name": "Окрашивание", "price": 3000, "duration_minutes": 120, "sort_order": 30},
    {"name": "Маникюр с покрытием", "price": 2000, "duration_minutes": 90, "sort_order": 40},
    {"name": "Оформление бровей", "price": 800, "duration_minutes": 30, "sort_order": 50},
]

NEW_SERVICES = [
    {"name": "Услуга за 500 ₽", "price": 500, "duration_minutes": 30, "sort_order": 10},
    {"name": "Услуга за 1 000 ₽", "price": 1000, "duration_minutes": 60, "sort_order": 20},
    {"name": "Услуга за 1 500 ₽", "price": 1500, "duration_minutes": 60, "sort_order": 30},
    {"name": "Услуга за 2 000 ₽", "price": 2000, "duration_minutes": 90, "sort_order": 40},
    {"name": "Услуга за 3 000 ₽", "price": 3000, "duration_minutes": 120, "sort_order": 50},
]


def _retire(names: list[str]) -> None:
    """Удалить услуги без записей, остальные — отключить."""
    in_use = sa.select(bookings.c.service_id)
    op.execute(
        services.delete().where(services.c.name.in_(names), services.c.id.not_in(in_use))
    )
    op.execute(
        services.update().where(services.c.name.in_(names)).values(is_active=False)
    )


def _restore(rows: list[dict]) -> None:
    """Включить услуги, которые остались отключёнными, и добавить удалённые."""
    conn = op.get_bind()
    names = [row["name"] for row in rows]
    op.execute(services.update().where(services.c.name.in_(names)).values(is_active=True))
    existing = set(conn.scalars(sa.select(services.c.name).where(services.c.name.in_(names))))
    missing = [row for row in rows if row["name"] not in existing]
    if missing:
        op.bulk_insert(services, missing)


def upgrade() -> None:
    _retire([row["name"] for row in OLD_SERVICES])
    _restore(NEW_SERVICES)


def downgrade() -> None:
    _retire([row["name"] for row in NEW_SERVICES])
    _restore(OLD_SERVICES)
