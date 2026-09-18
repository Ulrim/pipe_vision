"""active_order 테이블 추가 (현재 검사 오더 단일 행)

발주마다 품목(모양/외경/개수)과 절단 길이가 달라진다. 기존에는 워커가
env AIVIS_ITEM_CODE 로 품목이 고정돼 오더 전환 시 재시작이 필요했다.
웹(대시보드)에서 "지금 검사할 오더"를 PUT /master/active 로 설정하면
워커가 GET /master/active 를 폴링(15s 핫리로드 주기)해 재시작 없이
품목/LOT/작업지시를 전환한다.

- active_order: id INTEGER PK(항상 1, 단일 행 upsert),
  item_code TEXT NOT NULL REFERENCES item_master(item_code),
  lot/work_order TEXT NULL, updated_by TEXT, updated_at TIMESTAMPTZ.
- sqlite 독립형은 init_db(create_all)가 자동 생성 → 본 파일은 postgres 경로용.

Revision ID: 0005_active_order
Revises: 0004_insp_tube_index
Create Date: 2026-08-21
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_active_order"
down_revision = "0004_insp_tube_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "active_order",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column(
            "item_code",
            sa.Text(),
            sa.ForeignKey("item_master.item_code"),
            nullable=False,
        ),
        sa.Column("lot", sa.Text(), nullable=True),
        sa.Column("work_order", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("active_order")
