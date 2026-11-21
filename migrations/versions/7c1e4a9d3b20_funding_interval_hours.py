"""funding interval_hours

Revision ID: 7c1e4a9d3b20
Revises: 2f754eb268c5
Create Date: 2026-11-21 20:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7c1e4a9d3b20"
down_revision: str | None = "2f754eb268c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # No default: existing rows have no known settlement period, so the
    # upgrade fails loudly instead of guessing one.
    op.add_column(
        "funding_rates", sa.Column("interval_hours", sa.Integer(), nullable=False)
    )
    op.create_check_constraint(
        "funding_interval_positive", "funding_rates", "interval_hours > 0"
    )


def downgrade() -> None:
    op.drop_constraint("funding_interval_positive", "funding_rates", type_="check")
    op.drop_column("funding_rates", "interval_hours")
