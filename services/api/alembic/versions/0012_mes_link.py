"""MES 생산실적 연계 — 개수 확인 결과 저장 + 연계 테이블 컬럼

품질·생산실적 MES(2026-10-14, services/mes)가 연계 테이블만 보고 생산실적을 셀 수
있게 한다.

- inspection.count_detected / count_expected: 크레이트 개수 확인(CRATE_COUNT)의
  실제 개수 / 기준 개수. 종전엔 하트비트로만 나가 DB 에 남지 않았다.
- mes_quality_if: inspection_stage, tube_index, manual_verdict, count_detected,
  count_expected(실적 계산용), consumed_at(MES 가 가져간 시각), updated_at(재확인으로
  행이 바뀐 시각 — MES 는 읽은 뒤 바뀐 행을 consumed 로 표시하지 않는다).
- sqlite 독립형은 init_db 의 reconcile_sqlite_schema 가 열을 추가한다.

Revision ID: 0012_mes_link
Revises: 0011_tube_bbox
Create Date: 2026-10-14
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_mes_link"
down_revision = "0011_tube_bbox"
branch_labels = None
depends_on = None

_INSPECTION = (
    ("count_detected", sa.Integer()),
    ("count_expected", sa.Integer()),
)
_STAGING = (
    ("inspection_stage", sa.Text()),
    ("tube_index", sa.Integer()),
    ("manual_verdict", sa.Text()),
    ("count_detected", sa.Integer()),
    ("count_expected", sa.Integer()),
    ("consumed_at", sa.DateTime(timezone=True)),
    ("updated_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    for name, typ in _INSPECTION:
        op.add_column("inspection", sa.Column(name, typ, nullable=True))
    for name, typ in _STAGING:
        op.add_column("mes_quality_if", sa.Column(name, typ, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(_STAGING):
        op.drop_column("mes_quality_if", name)
    for name, _ in reversed(_INSPECTION):
        op.drop_column("inspection", name)
