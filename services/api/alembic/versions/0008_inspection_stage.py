"""inspection 에 검사 단계(inspection_stage) 추가

데이터 정의서(3-3 촬영 메타 인덱스 / 4-3 결함 라벨 / 5-3 판정 레코드)가 세 곳에서
필수로 요구하는 항목인데 시스템에 개념 자체가 없었다.

공정상 두 지점에서 서로 다른 것을 본다. 촬영 조건이 정반대라 한 스테이션이 겸할
수 없다 — 길이는 실루엣을 얻으려 뒤에서 빛을 쏘고(백라이트), 표면은 앞에서 고르게
비추거나(확산광) 흠집 그림자를 세우려 낮게 비춘다(사광). 같은 제품이라도 어느
단계에서 찍혔는지에 따라 판정 근거와 학습 분포가 달라지므로 이미지·라벨·판정
레코드 전부에 실어야 한다.

- inspection.inspection_stage TEXT NULL — CUT_LENGTH | POST_WASH_SURFACE
- 기존 행은 NULL 로 둔다. 단계 구분이 없던 시절의 데이터를 사후에 어느 한쪽으로
  단정하면 정확도 집계와 학습 분포가 조용히 오염된다.
- 값 제약은 CHECK 대신 애플리케이션(Pydantic enum)에서 건다. 정의서 개정으로
  단계가 늘어날 때 마이그레이션 없이 따라가기 위함이다.
- sqlite 독립형은 init_db(create_all)가 자동 생성 → 본 파일은 postgres 경로용.

Revision ID: 0008_inspection_stage
Revises: 0007_inspection_label
Create Date: 2026-09-17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_inspection_stage"
down_revision = "0007_inspection_label"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "inspection", sa.Column("inspection_stage", sa.Text(), nullable=True)
    )
    # 단계별 정확도·불량률 집계가 기본 조회 축이 된다(품목 × 단계).
    op.create_index(
        "ix_insp_stage", "inspection", ["inspection_stage", "final_verdict"]
    )


def downgrade() -> None:
    op.drop_index("ix_insp_stage", table_name="inspection")
    op.drop_column("inspection", "inspection_stage")
