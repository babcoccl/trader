"""shadow ledger tables (spec G3)

Revision ID: 8a3f1c2d9e45
Revises: 7c1e4a9d3b20
Create Date: 2026-11-23 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "8a3f1c2d9e45"
down_revision: str | None = "7c1e4a9d3b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MONEY = sa.Numeric(38, 18)


def upgrade() -> None:
    op.create_table(
        "shadow_trades",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("sleeve", sa.String(length=20), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_price", MONEY, nullable=False),
        sa.Column("quantity", MONEY, nullable=False),
        sa.Column("cost_usd", MONEY, nullable=False),
        sa.Column("entry_fee_usd", MONEY, nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_price", MONEY, nullable=True),
        sa.Column("exit_fee_usd", MONEY, nullable=True),
        sa.Column("pnl_usd", MONEY, nullable=True),
        sa.Column("exit_reason", sa.String(length=60), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_shadow_trades_sleeve_closed",
        "shadow_trades",
        ["sleeve", "closed_at"],
    )
    op.create_table(
        "shadow_marks",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("sleeve", sa.String(length=20), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("equity_usd", MONEY, nullable=False),
        sa.Column("cash_usd", MONEY, nullable=False),
        sa.Column("exposure_usd", MONEY, nullable=False),
        sa.Column("open_trades", sa.Integer(), nullable=False),
        sa.Column("closed_trades", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sleeve", "day"),
    )
    op.create_table(
        "shadow_state",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sleeve", sa.String(length=20), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column(
            "state",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sleeve", "symbol"),
    )


def downgrade() -> None:
    op.drop_table("shadow_state")
    op.drop_table("shadow_marks")
    op.drop_index("ix_shadow_trades_sleeve_closed", table_name="shadow_trades")
    op.drop_table("shadow_trades")
