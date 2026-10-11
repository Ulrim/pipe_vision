"""inspection.tube_bbox — 다발 사진 속 튜브 위치

표면결함 라벨링(2026-10-11): 다발 한 장이 튜브 수만큼 행으로 저장되는데, 라벨링
화면이 그 행이 몇 번 튜브인지·사진 어디인지 몰라 라벨이 뒤섞일 수 있었다. 워커가
튜브 위치 [x0,y0,x1,y1](원본 화소)를 함께 보내 저장한다. 단일 튜브 행은 NULL.

- sqlite 독립형은 init_db 의 스키마 보정(reconcile_sqlite_schema)이 열을 추가한다.

Revision ID: 0011_tube_bbox
Revises: 0010_station_config
Create Date: 2026-10-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011_tube_bbox"
down_revision = "0010_station_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "inspection",
        sa.Column("tube_bbox", postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
                  nullable=True),
    )


def downgrade() -> None:
    op.drop_column("inspection", "tube_bbox")
