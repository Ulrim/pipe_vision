"""station_config 테이블 — 스테이션(카메라)별 검사 모드

파이+카메라 2대 이상 구성(2026-10-08). 오더(품목/LOT)는 공유하되 모드는
스테이션마다 다르므로 cam_id 별로 분리한다. 해석 순서:
  station_config[cam_id] > active_order.inspection_stage > 워커 env.

- station_config: cam_id TEXT PK, inspection_stage TEXT NULL,
  updated_by TEXT, updated_at TIMESTAMPTZ.
- sqlite 독립형은 init_db(create_all)가 자동 생성 → postgres 경로용.

Revision ID: 0010_station_config
Revises: 0009_active_order_stage
Create Date: 2026-10-08
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_station_config"
down_revision = "0009_active_order_stage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "station_config",
        sa.Column("cam_id", sa.Text(), primary_key=True),
        sa.Column("inspection_stage", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("station_config")
