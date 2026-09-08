"""kpi_manual 에 출하수량/출하유출 부적합수량 추가 (계약 성과지표)

계약 성과지표는 **출하유출불량률(ppm)** 인데, 시스템은 이를 산출할 수 없었다.
검사 단계에서 걸러낸 불량(공정불량률)과 달리, 출하 후 고객에서 발견된
부적합은 시스템이 볼 수 없는 정보이기 때문이다. 그래서 §7.1 의 비자동 입력
테이블(kpi_manual)에 분자/분모 두 항목을 받는다.

  출하유출불량률(ppm) = leak_defect_qty ÷ shipped_qty × 1,000,000

기존 공정불량률(ppm)은 그대로 두고 **병행 산출**한다. 두 지표는 이름도
산출식도 다르며, 어느 쪽을 인수 기준으로 삼을지는 계약 확인 사항이다.

- kpi_manual.shipped_qty      INTEGER NULL  총 출하수량
- kpi_manual.leak_defect_qty  INTEGER NULL  출하 후 발견된 부적합 수량
- sqlite 독립형은 init_db(create_all)가 자동 생성 → 본 파일은 postgres 경로용.

Revision ID: 0006_kpi_shipment
Revises: 0005_active_order
Create Date: 2026-09-08
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_kpi_shipment"
down_revision = "0005_active_order"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("kpi_manual", sa.Column("shipped_qty", sa.Integer(), nullable=True))
    op.add_column(
        "kpi_manual", sa.Column("leak_defect_qty", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("kpi_manual", "leak_defect_qty")
    op.drop_column("kpi_manual", "shipped_qty")
