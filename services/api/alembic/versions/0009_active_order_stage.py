"""active_order 에 검사 단계(inspection_stage) 추가 — 검사 모드 전환

현장 요구(2026-10-05): 길이·표면·개수를 한 번에 돌리니 NG 가 왜 났는지 모르겠다.
단계(InspectionStage)를 검사 모드로 삼아 한 모드는 한 항목만 판정하도록 바꿨고,
그 모드를 **재시작 없이** 바꿀 자리가 필요하다. 활성 오더는 워커가 이미 15s
주기로 폴링하므로 여기에 싣는다.

- active_order.inspection_stage TEXT NULL — CUT_LENGTH | POST_WASH_SURFACE | CRATE_COUNT
- NULL 이면 워커 env(AIVIS_INSPECTION_STAGE) 기본값을 쓴다(스테이션 고정 운영).
- 값 제약은 애플리케이션(enum)에서 — 모드가 늘 때 마이그레이션 없이 따라가기.
- sqlite 독립형은 init_db(create_all + reconcile)가 자동 처리 → postgres 경로용.

Revision ID: 0009_active_order_stage
Revises: 0008_inspection_stage
Create Date: 2026-10-05
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_active_order_stage"
down_revision = "0008_inspection_stage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "active_order", sa.Column("inspection_stage", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("active_order", "inspection_stage")
