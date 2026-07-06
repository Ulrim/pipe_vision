"""SQLAlchemy 2.0 ORM 모델 — CLAUDE.md §7.1 스키마 그대로 매핑.

테이블: item_master, inspection, kpi_manual, app_user, sys_log, mes_quality_if(§7.3).
인덱스: ix_insp_lot, ix_insp_time, ix_insp_item_verdict (§7.1),
ux_insp_natkey (자연키 멱등 유니크, 0002 마이그레이션).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.types import JsonB, StringArray


class ItemMaster(Base):
    """품목/기준정보 (item_master)."""

    __tablename__ = "item_master"

    item_code: Mapped[str] = mapped_column(Text, primary_key=True)
    item_name: Mapped[str] = mapped_column(Text, nullable=False)
    ref_length_mm: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False)
    tol_plus_mm: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False)
    tol_minus_mm: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False)
    px_to_mm_scale: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False)
    oil_threshold: Mapped[float | None] = mapped_column(Numeric(5, 4))
    discolor_threshold: Mapped[float | None] = mapped_column(Numeric(5, 4))
    scratch_threshold: Mapped[float | None] = mapped_column(Numeric(5, 4))
    capture_recipe: Mapped[dict | None] = mapped_column(JsonB)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Inspection(Base):
    """검사 결과 (inspection, 제품 1개 = 1행)."""

    __tablename__ = "inspection"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    lot: Mapped[str] = mapped_column(Text, nullable=False)
    work_order: Mapped[str | None] = mapped_column(Text)
    item_code: Mapped[str | None] = mapped_column(
        Text, ForeignKey("item_master.item_code")
    )
    cam_id: Mapped[str] = mapped_column(Text, nullable=False)
    inspected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    shift: Mapped[str | None] = mapped_column(Text)
    operator: Mapped[str | None] = mapped_column(Text)

    # 길이
    ref_length_mm: Mapped[float | None] = mapped_column(Numeric(10, 3))
    meas_length_mm: Mapped[float | None] = mapped_column(Numeric(10, 3))
    deviation_mm: Mapped[float | None] = mapped_column(Numeric(10, 3))
    length_verdict: Mapped[str | None] = mapped_column(Text)

    # 표면 (0~1 신뢰도)
    oil_score: Mapped[float | None] = mapped_column(Numeric(5, 4))
    discolor_score: Mapped[float | None] = mapped_column(Numeric(5, 4))
    scratch_score: Mapped[float | None] = mapped_column(Numeric(5, 4))

    # 종합
    final_verdict: Mapped[str] = mapped_column(Text, nullable=False)
    defect_codes: Mapped[list[str] | None] = mapped_column(StringArray)
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4))
    raw_image_path: Mapped[str | None] = mapped_column(Text)
    result_image_path: Mapped[str | None] = mapped_column(Text)
    proc_time_ms: Mapped[int | None] = mapped_column(Integer)

    # 운영/재확인
    review_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    manual_verdict: Mapped[str | None] = mapped_column(Text)
    mes_synced: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        Index("ix_insp_lot", "lot"),
        Index("ix_insp_time", "inspected_at"),
        Index("ix_insp_item_verdict", "item_code", "final_verdict"),
        # 자연키 멱등(POST /inspection 재전송 중복 방지, MES idem_key 와 동일 구성).
        # cam_id+inspected_at 선두 → 자연키 동등 조회가 인덱스만으로 즉시 좁혀짐.
        Index(
            "ux_insp_natkey",
            "cam_id",
            "inspected_at",
            "lot",
            "item_code",
            unique=True,
        ),
    )


class KpiManual(Base):
    """KPI 비자동 항목 (kpi_manual)."""

    __tablename__ = "kpi_manual"

    period: Mapped[datetime] = mapped_column(DateTime(timezone=False), primary_key=True)
    claim_count: Mapped[int | None] = mapped_column(Integer)
    workload_index: Mapped[float | None] = mapped_column(Numeric)
    lead_time_days: Mapped[float | None] = mapped_column(Numeric)
    note: Mapped[str | None] = mapped_column(Text)


class AppUser(Base):
    """사용자/권한 (app_user)."""

    __tablename__ = "app_user"

    username: Mapped[str] = mapped_column(Text, primary_key=True)
    pw_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (
        CheckConstraint(
            "role IN ('operator','quality','admin')", name="ck_app_user_role"
        ),
    )


class SysLog(Base):
    """시스템 로그 (sys_log)."""

    __tablename__ = "sys_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    level: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JsonB)


class MesQualityIf(Base):
    """MES 연계 스테이징 테이블 (mes_quality_if, §7.3).

    DB 인터페이스 테이블 방식: 검사결과 식별자+판정 핵심값을 INSERT 하면
    MES 가 폴링/트리거로 적재한다. 멱등키 = lot+item_code+inspected_at+cam_id.
    """

    __tablename__ = "mes_quality_if"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    inspection_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("inspection.id")
    )
    # 멱등키 구성 요소
    lot: Mapped[str] = mapped_column(Text, nullable=False)
    item_code: Mapped[str | None] = mapped_column(Text)
    inspected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    cam_id: Mapped[str] = mapped_column(Text, nullable=False)
    idem_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    # 판정 핵심값
    work_order: Mapped[str | None] = mapped_column(Text)
    final_verdict: Mapped[str] = mapped_column(Text, nullable=False)
    defect_codes: Mapped[list[str] | None] = mapped_column(StringArray)
    meas_length_mm: Mapped[float | None] = mapped_column(Numeric(10, 3))
    deviation_mm: Mapped[float | None] = mapped_column(Numeric(10, 3))
    # 연계 상태
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_mesif_consumed", "consumed"),
    )
