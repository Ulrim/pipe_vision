"""item_master 오더 설정 필드 추가 (expected_count, outer_diameter_mm)

오더별 검사 사양: 한 프레임당 튜브 개수(expected_count, 기본 1)와
튜브 외경(outer_diameter_mm, nullable — 단면/직경 검증·세그멘테이션 힌트).
sqlite/postgres 양쪽 지원.

Revision ID: 0003_item_order_fields
Revises: 0002_ux_insp_natkey
Create Date: 2026-07-06
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_item_order_fields"
down_revision = "0002_ux_insp_natkey"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "item_master",
        sa.Column(
            "expected_count",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column(
        "item_master",
        sa.Column("outer_diameter_mm", sa.Numeric(10, 3), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("item_master", "outer_diameter_mm")
    op.drop_column("item_master", "expected_count")
