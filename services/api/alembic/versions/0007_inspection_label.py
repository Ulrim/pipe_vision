"""inspection_label 테이블 추가 (사람이 붙인 정답 라벨)

표면 결함 모델(04)이 막혀 있는 이유는 카메라도 알고리즘도 아니고 **정답셋**이다.
현장에는 검사 이미지가 수십 GB 쌓여 있지만 "이 사진이 유분기인지 변색인지"를
사람이 적어 둔 기록이 없어 학습도 정확도 측정도 할 수 없었다.

기존 inspection.manual_verdict 로는 부족하다. 그것은 작업자가 NG 를 재확인한
OK/NG 한 글자여서 불량유형도, 경계 사례 여부도 담지 못한다. §1.2 의 "항목별
판정 정확도 95%"는 항목이 있어야 측정된다.

- inspection_label.inspection_id  PK/FK -> inspection(id) ON DELETE CASCADE
- labels        TEXT[]   불량유형 배열(빈 배열 = 정상). §7.2 defect_codes 매핑
- border        BOOLEAN  경계 샘플(부록 A.2)
- length_mm_gt  NUMERIC  길이 정답(mm)
- note / labeled_by / labeled_at
- sqlite 독립형은 init_db(create_all)가 자동 생성 → 본 파일은 postgres 경로용.

Revision ID: 0007_inspection_label
Revises: 0006_kpi_shipment
Create Date: 2026-09-08
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_inspection_label"
down_revision = "0006_kpi_shipment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inspection_label",
        sa.Column("inspection_id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "labels",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "border", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("length_mm_gt", sa.Numeric(10, 3), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("labeled_by", sa.Text(), nullable=True),
        sa.Column(
            "labeled_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["inspection_id"], ["inspection.id"], ondelete="CASCADE"
        ),
    )
    # 라벨링 진척(클래스별 수량) 집계와 미라벨 큐 조회에 쓰인다.
    op.create_index(
        "ix_label_labeled_at", "inspection_label", ["labeled_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_label_labeled_at", table_name="inspection_label")
    op.drop_table("inspection_label")
