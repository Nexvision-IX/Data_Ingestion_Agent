"""Add canonical vendor, date, currency, and extraction-attempt fields.

Revision ID: 20260720_01
Revises:
Create Date: 2026-07-20
"""

from __future__ import annotations

import json
import re

import sqlalchemy as sa
from alembic import op


revision = "20260720_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE invoices SET extraction_confidence = NULL "
            "WHERE extraction_confidence < 0 OR extraction_confidence > 1"
        )
    )
    with op.batch_alter_table("invoices") as batch:
        batch.alter_column("vendor_number", existing_type=sa.String(50), nullable=True)
        batch.alter_column("invoice_date", existing_type=sa.Date(), nullable=True)
        batch.alter_column(
            "currency",
            existing_type=sa.String(10),
            nullable=True,
            server_default=None,
        )
        batch.alter_column(
            "extraction_confidence",
            existing_type=sa.Float(),
            nullable=True,
            server_default=None,
        )
        batch.add_column(sa.Column("extracted_vendor_number", sa.String(100)))
        batch.add_column(sa.Column("resolved_vendor_number", sa.String(100)))
        batch.add_column(sa.Column("vendor_match_method", sa.String(50)))
        batch.add_column(sa.Column("vendor_match_status", sa.String(50)))
        batch.add_column(sa.Column("vendor_match_evidence", sa.JSON()))
        batch.add_column(sa.Column("normalized_invoice_number", sa.String(120)))
        batch.add_column(sa.Column("raw_invoice_date", sa.String(100)))
        batch.add_column(sa.Column("raw_due_date", sa.String(100)))
        batch.add_column(sa.Column("due_date", sa.Date()))
        batch.add_column(sa.Column("date_parse_status", sa.String(30)))
        batch.add_column(sa.Column("date_parse_warning", sa.Text()))
        batch.add_column(sa.Column("date_parse_evidence", sa.JSON()))
        batch.add_column(sa.Column("extracted_currency", sa.String(20)))
        batch.add_column(sa.Column("resolved_currency", sa.String(10)))
        batch.add_column(sa.Column("currency_resolution_method", sa.String(50)))
        batch.add_column(sa.Column("currency_resolution_evidence", sa.JSON()))
        batch.add_column(sa.Column("extraction_field_confidence", sa.JSON()))
        batch.add_column(sa.Column("extraction_warnings", sa.JSON()))
        batch.add_column(sa.Column("extraction_provider", sa.String(100)))
        batch.add_column(sa.Column("extraction_model", sa.String(100)))
        batch.add_column(sa.Column("extraction_version", sa.String(100)))
        batch.add_column(
            sa.Column(
                "extraction_attempt_number",
                sa.Integer(),
                nullable=False,
                server_default="1",
            )
        )
        batch.add_column(sa.Column("extraction_review_status", sa.String(50)))
        batch.create_index(
            "ix_invoices_resolved_vendor_number", ["resolved_vendor_number"]
        )
        batch.create_index(
            "ix_invoices_normalized_invoice_number", ["normalized_invoice_number"]
        )
        batch.create_index(
            "ix_invoices_extraction_review_status", ["extraction_review_status"]
        )
        batch.create_check_constraint(
            "ck_invoices_extraction_confidence_bounded",
            "extraction_confidence IS NULL OR "
            "(extraction_confidence >= 0 AND extraction_confidence <= 1)",
        )

    op.create_table(
        "extraction_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "invoice_id",
            sa.String(36),
            sa.ForeignKey("invoices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("overall_confidence", sa.Float()),
        sa.Column("field_confidence", sa.JSON()),
        sa.Column("warnings", sa.JSON()),
        sa.Column("ocr_provider", sa.String(100)),
        sa.Column("ocr_version", sa.String(100)),
        sa.Column("extraction_provider", sa.String(100)),
        sa.Column("extraction_model", sa.String(100)),
        sa.Column("schema_version", sa.String(100)),
        sa.Column("raw_evidence", sa.JSON()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "invoice_id",
            "attempt_number",
            name="uq_extraction_attempt_invoice_number",
        ),
    )
    op.create_index(
        "ix_extraction_attempts_invoice_id",
        "extraction_attempts",
        ["invoice_id"],
    )
    _migrate_existing_rows()


def _migrate_existing_rows() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT id, vendor_name, vendor_number, invoice_number, "
            "invoice_date, currency, extraction_raw FROM invoices"
        )
    ).mappings()
    for row in rows:
        vendor_name = str(row.get("vendor_name") or "")
        legacy_vendor = row.get("vendor_number")
        generated_key = re.sub(
            r"[^A-Z0-9]+", "_", vendor_name.upper()
        ).strip("_")
        normalized_legal_key = generated_key.replace(
            "_PRIVATE_LIMITED", "_PVT_LTD"
        ).replace("_LIMITED", "_LTD")
        synthetic = (
            bool(legacy_vendor)
            and str(legacy_vendor).upper()
            in {generated_key, normalized_legal_key}
        )
        evidence = {
            "migration": "20260720_01",
            "legacy_vendor_number": legacy_vendor if synthetic else None,
            "legacy_vendor_number_trusted": not synthetic,
            "requires_po_revalidation": True,
        }
        connection.execute(
            sa.text(
                "UPDATE invoices SET "
                "vendor_number=:vendor_number, "
                "extracted_vendor_number=:extracted_vendor_number, "
                "resolved_vendor_number=NULL, "
                "vendor_match_status='UNRESOLVED', "
                "vendor_match_evidence=:vendor_evidence, "
                "normalized_invoice_number=:normalized_invoice_number, "
                "raw_invoice_date=:raw_invoice_date, "
                "date_parse_status='LEGACY_UNVALIDATED', "
                "extracted_currency=:extracted_currency, "
                "resolved_currency=NULL, "
                "currency_resolution_method='UNRESOLVED', "
                "currency_resolution_evidence=:currency_evidence, "
                "extraction_review_status='REVALIDATION_REQUIRED' "
                "WHERE id=:id"
            ),
            {
                "id": row["id"],
                "vendor_number": None if synthetic else legacy_vendor,
                "extracted_vendor_number": None if synthetic else legacy_vendor,
                "vendor_evidence": json.dumps(evidence),
                "normalized_invoice_number": str(
                    row.get("invoice_number") or ""
                ).strip().upper(),
                "raw_invoice_date": (
                    str(row.get("invoice_date"))
                    if row.get("invoice_date") is not None
                    else None
                ),
                "extracted_currency": row.get("currency"),
                "currency_evidence": json.dumps({
                    "legacy_currency": row.get("currency"),
                    "requires_revalidation": True,
                }),
            },
        )


def downgrade() -> None:
    op.drop_index(
        "ix_extraction_attempts_invoice_id",
        table_name="extraction_attempts",
    )
    op.drop_table("extraction_attempts")
    with op.batch_alter_table("invoices") as batch:
        batch.drop_constraint(
            "ck_invoices_extraction_confidence_bounded",
            type_="check",
        )
        batch.drop_index("ix_invoices_extraction_review_status")
        batch.drop_index("ix_invoices_normalized_invoice_number")
        batch.drop_index("ix_invoices_resolved_vendor_number")
        for column in (
            "extraction_review_status",
            "extraction_attempt_number",
            "extraction_version",
            "extraction_model",
            "extraction_provider",
            "extraction_warnings",
            "extraction_field_confidence",
            "currency_resolution_evidence",
            "currency_resolution_method",
            "resolved_currency",
            "extracted_currency",
            "date_parse_evidence",
            "date_parse_warning",
            "date_parse_status",
            "due_date",
            "raw_due_date",
            "raw_invoice_date",
            "normalized_invoice_number",
            "vendor_match_evidence",
            "vendor_match_status",
            "vendor_match_method",
            "resolved_vendor_number",
            "extracted_vendor_number",
        ):
            batch.drop_column(column)
