"""inspection.meas_temp_c — 길이 측정 시 제품 온도

±0.1mm 공차의 %GR&R 29% 는 제품 온도를 ±1K 안에서 알고 20°C 로 환산한다는 전제다
(docs/LENGTH_TOLERANCE.md). 워커가 온도 소스(AIVIS_TEMP_SOURCE)로 환산했으면 그 온도를
함께 보내 저장한다 — 몇 도에서 잰 값인지 없으면 길이값을 다른 날과 비교할 수 없다.
NULL = 환산하지 않았다(온도 소스 없음·센서 오류).

- sqlite 독립형은 init_db 의 스키마 보정(reconcile_sqlite_schema)이 열을 추가한다.

Revision ID: 0013_meas_temp
Revises: 0012_mes_link
Create Date: 2026-10-20
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_meas_temp"
down_revision = "0012_mes_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("inspection", sa.Column("meas_temp_c", sa.Numeric(5, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("inspection", "meas_temp_c")
