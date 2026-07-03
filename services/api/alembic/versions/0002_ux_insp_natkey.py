"""inspection 자연키 유니크 인덱스 (POST /inspection 멱등, 엣지 스풀 재전송 대응)

자연키 = (cam_id, inspected_at, lot, item_code) — MES idem_key(§7.3)와 동일 구성.
재전송(서버 저장 후 응답 유실)이 행을 중복 생성하지 못하게 유니크로 강제하고,
save_inspection() 의 자연키 사전 조회를 인덱스 스캔으로 만든다.

Revision ID: 0002_ux_insp_natkey
Revises: 0001_initial
Create Date: 2026-07-03
"""
from __future__ import annotations

from alembic import op

revision = "0002_ux_insp_natkey"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ux_insp_natkey",
        "inspection",
        ["cam_id", "inspected_at", "lot", "item_code"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ux_insp_natkey", table_name="inspection")
