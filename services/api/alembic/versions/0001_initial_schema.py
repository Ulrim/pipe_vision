"""initial schema (CLAUDE.md §7.1 + mes_quality_if §7.3)

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-04
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.types import Text

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def _string_array():
    return ARRAY(Text()).with_variant(JSON(), "sqlite")


def _jsonb():
    return JSONB().with_variant(JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "item_master",
        sa.Column("item_code", sa.Text(), primary_key=True),
        sa.Column("item_name", sa.Text(), nullable=False),
        sa.Column("ref_length_mm", sa.Numeric(10, 3), nullable=False),
        sa.Column("tol_plus_mm", sa.Numeric(10, 3), nullable=False),
        sa.Column("tol_minus_mm", sa.Numeric(10, 3), nullable=False),
        sa.Column("px_to_mm_scale", sa.Numeric(12, 6), nullable=False),
        sa.Column("oil_threshold", sa.Numeric(5, 4)),
        sa.Column("discolor_threshold", sa.Numeric(5, 4)),
        sa.Column("scratch_threshold", sa.Numeric(5, 4)),
        sa.Column("capture_recipe", _jsonb()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by", sa.Text()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "inspection",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("lot", sa.Text(), nullable=False),
        sa.Column("work_order", sa.Text()),
        sa.Column("item_code", sa.Text(), sa.ForeignKey("item_master.item_code")),
        sa.Column("cam_id", sa.Text(), nullable=False),
        sa.Column("inspected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("shift", sa.Text()),
        sa.Column("operator", sa.Text()),
        sa.Column("ref_length_mm", sa.Numeric(10, 3)),
        sa.Column("meas_length_mm", sa.Numeric(10, 3)),
        sa.Column("deviation_mm", sa.Numeric(10, 3)),
        sa.Column("length_verdict", sa.Text()),
        sa.Column("oil_score", sa.Numeric(5, 4)),
        sa.Column("discolor_score", sa.Numeric(5, 4)),
        sa.Column("scratch_score", sa.Numeric(5, 4)),
        sa.Column("final_verdict", sa.Text(), nullable=False),
        sa.Column("defect_codes", _string_array()),
        sa.Column("confidence", sa.Numeric(5, 4)),
        sa.Column("raw_image_path", sa.Text()),
        sa.Column("result_image_path", sa.Text()),
        sa.Column("proc_time_ms", sa.Integer()),
        sa.Column("review_flag", sa.Boolean(), server_default=sa.false()),
        sa.Column("manual_verdict", sa.Text()),
        sa.Column("mes_synced", sa.Boolean(), server_default=sa.false()),
    )
    op.create_index("ix_insp_lot", "inspection", ["lot"])
    op.create_index("ix_insp_time", "inspection", ["inspected_at"])
    op.create_index(
        "ix_insp_item_verdict", "inspection", ["item_code", "final_verdict"]
    )

    op.create_table(
        "kpi_manual",
        sa.Column("period", sa.Date(), primary_key=True),
        sa.Column("claim_count", sa.Integer()),
        sa.Column("workload_index", sa.Numeric()),
        sa.Column("lead_time_days", sa.Numeric()),
        sa.Column("note", sa.Text()),
    )

    op.create_table(
        "app_user",
        sa.Column("username", sa.Text(), primary_key=True),
        sa.Column("pw_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true()),
        sa.CheckConstraint(
            "role IN ('operator','quality','admin')", name="ck_app_user_role"
        ),
    )

    op.create_table(
        "sys_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("level", sa.Text()),
        sa.Column("category", sa.Text()),
        sa.Column("message", sa.Text()),
        sa.Column("payload", _jsonb()),
    )

    op.create_table(
        "mes_quality_if",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("inspection_id", sa.BigInteger(), sa.ForeignKey("inspection.id")),
        sa.Column("lot", sa.Text(), nullable=False),
        sa.Column("item_code", sa.Text()),
        sa.Column("inspected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cam_id", sa.Text(), nullable=False),
        sa.Column("idem_key", sa.Text(), nullable=False, unique=True),
        sa.Column("work_order", sa.Text()),
        sa.Column("final_verdict", sa.Text(), nullable=False),
        sa.Column("defect_codes", _string_array()),
        sa.Column("meas_length_mm", sa.Numeric(10, 3)),
        sa.Column("deviation_mm", sa.Numeric(10, 3)),
        sa.Column("consumed", sa.Boolean(), server_default=sa.false()),
        sa.Column("retry_count", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_mesif_consumed", "mes_quality_if", ["consumed"])


def downgrade() -> None:
    op.drop_index("ix_mesif_consumed", table_name="mes_quality_if")
    op.drop_table("mes_quality_if")
    op.drop_table("sys_log")
    op.drop_table("app_user")
    op.drop_table("kpi_manual")
    op.drop_index("ix_insp_item_verdict", table_name="inspection")
    op.drop_index("ix_insp_time", table_name="inspection")
    op.drop_index("ix_insp_lot", table_name="inspection")
    op.drop_table("inspection")
    op.drop_table("item_master")
