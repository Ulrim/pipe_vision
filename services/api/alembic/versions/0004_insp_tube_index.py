"""inspection.tube_index 추가 + 자연키에 tube_index 포함 (다중 튜브 배치 저장)

한 프레임(배치)의 튜브 N개는 lot/item_code/cam_id/inspected_at 이 동일하다.
기존 자연키 유니크(ux_insp_natkey)로는 배치의 첫 튜브만 저장되고 나머지가
멱등 병합돼 유실된다. tube_index(0..N-1)를 컬럼·자연키에 추가해 튜브별로
별도 행을 저장한다. 단일 튜브(tube_index=0)는 기존과 동일 동작.

- inspection.tube_index INTEGER NOT NULL DEFAULT 0.
- ux_insp_natkey 를 (cam_id, inspected_at, lot, item_code, tube_index) 로 재정의.
sqlite/postgres 공통.

Revision ID: 0004_insp_tube_index
Revises: 0003_item_order_fields
Create Date: 2026-07-06
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_insp_tube_index"
down_revision = "0003_item_order_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "inspection",
        sa.Column(
            "tube_index",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.drop_index("ux_insp_natkey", table_name="inspection")
    op.create_index(
        "ux_insp_natkey",
        "inspection",
        ["cam_id", "inspected_at", "lot", "item_code", "tube_index"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ux_insp_natkey", table_name="inspection")
    op.create_index(
        "ux_insp_natkey",
        "inspection",
        ["cam_id", "inspected_at", "lot", "item_code"],
        unique=True,
    )
    op.drop_column("inspection", "tube_index")
