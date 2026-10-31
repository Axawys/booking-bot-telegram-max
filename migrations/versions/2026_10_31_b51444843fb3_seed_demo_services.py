"""seed demo services

Revision ID: b51444843fb3
Revises: 75e99b4f4854
Create Date: 2026-10-31 23:58:23.905859

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b51444843fb3'
down_revision: Union[str, Sequence[str], None] = '75e99b4f4854'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


services = sa.table(
    "services",
    sa.column("name", sa.String),
    sa.column("price", sa.Integer),
    sa.column("duration_minutes", sa.Integer),
    sa.column("sort_order", sa.Integer),
)

DEMO_SERVICES = [
    {"name": "Женская стрижка", "price": 1500, "duration_minutes": 60, "sort_order": 10},
    {"name": "Мужская стрижка", "price": 1000, "duration_minutes": 30, "sort_order": 20},
    {"name": "Окрашивание", "price": 3000, "duration_minutes": 120, "sort_order": 30},
    {"name": "Маникюр с покрытием", "price": 2000, "duration_minutes": 90, "sort_order": 40},
    {"name": "Оформление бровей", "price": 800, "duration_minutes": 30, "sort_order": 50},
]


def upgrade() -> None:
    """Демо-услуги для портфолио."""
    op.bulk_insert(services, DEMO_SERVICES)


def downgrade() -> None:
    names = [service["name"] for service in DEMO_SERVICES]
    op.execute(services.delete().where(services.c.name.in_(names)))
