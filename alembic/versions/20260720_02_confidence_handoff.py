"""Add confidence provenance and retry count.

Revision ID: 20260720_02
Revises: 20260720_01
Create Date: 2026-07-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260720_02"
down_revision = "20260720_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("invoices") as batch:
        batch.add_column(
            sa.Column(
                "extraction_confidence_source",
                sa.String(100),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column(
                "extraction_retry_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )

    op.execute(
        sa.text(
            "UPDATE invoices SET extraction_confidence_source = "
            "CASE WHEN extraction_confidence IS NULL THEN 'UNAVAILABLE' "
            "ELSE 'LEGACY_EXTRACTION_CONFIDENCE' END"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("invoices") as batch:
        batch.drop_column("extraction_retry_count")
        batch.drop_column("extraction_confidence_source")
