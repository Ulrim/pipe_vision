"""MES 연계 어댑터 (CLAUDE.md §7.3, §5 M9).

검사 1건(inspection 행)을 MES 로 연계하는 단일 진입점. MES_MODE 로 동작 분기:

- table 모드: mes_quality_if 스테이징 테이블에 멱등 INSERT(이미 backend 가
  POST /inspection 트랜잭션에서 적재). 어댑터는 누락분을 보충 INSERT 하고,
  스테이징 적재가 끝나면 "MES 가 폴링한다"는 계약상 inspection.mes_synced 를
  표시한다(스테이징 보장 = 연계 보장).
- rest 모드: MesTransport 로 외부 MES 에 POST. 성공 시 mes_synced=true.

멱등키 = lot|item_code|inspected_at|cam_id (backend core.inspection_service 재사용).
중복 적재 금지: idem_key UNIQUE + 사전 존재 확인.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aivis_types import LogCategory

from core.logging import write_log
from db.models import Inspection, MesQualityIf
from mes.config import MesConfig, get_mes_config
from mes.transport import FakeMesTransport, HttpxMesTransport, MesTransport, MesTransportError


def make_idem_key_from_row(row: Inspection) -> str:
    """inspection 행으로 멱등키 생성 (core.inspection_service.make_idem_key 와 동일 규칙).

    backend 의 make_idem_key 는 pydantic InspectionResult 를 받으므로,
    ORM 행에서 동일 포맷을 재현한다:
    lot|item_code|inspected_at(iso)|cam_id|tube_index.
    """
    ts = row.inspected_at
    ts_str = ts.isoformat() if isinstance(ts, datetime) else str(ts)
    tube_index = row.tube_index if row.tube_index is not None else 0
    return f"{row.lot}|{row.item_code}|{ts_str}|{row.cam_id}|{tube_index}"


def _row_to_payload(row: Inspection, idem_key: str) -> dict[str, Any]:
    """MES 전송/스테이징용 핵심값 페이로드(§7.3 식별자+판정 핵심값)."""
    return {
        "inspection_id": row.id,
        "lot": row.lot,
        "item_code": row.item_code,
        "inspected_at": row.inspected_at.isoformat()
        if isinstance(row.inspected_at, datetime)
        else str(row.inspected_at),
        "cam_id": row.cam_id,
        "idem_key": idem_key,
        "work_order": row.work_order,
        "final_verdict": row.final_verdict,
        "defect_codes": list(row.defect_codes or []),
        "meas_length_mm": float(row.meas_length_mm)
        if row.meas_length_mm is not None
        else None,
        "deviation_mm": float(row.deviation_mm)
        if row.deviation_mm is not None
        else None,
    }


def _build_transport(cfg: MesConfig) -> MesTransport:
    """rest 모드 전송 객체 생성. URL 미설정이면 FakeMesTransport(끊김 방지)."""
    if cfg.rest_url:
        return HttpxMesTransport(
            cfg.rest_url,
            timeout_s=cfg.rest_timeout_s,
            idem_header=cfg.idem_header,
        )
    # 통합 전: 더미 전송으로 파이프라인 유지(연계율 100% 설계 — 운영 전 단계).
    return FakeMesTransport()


class MesAdapter:
    """단일 inspection 행을 MES 로 연계. table/rest 공통 인터페이스."""

    def __init__(
        self,
        cfg: MesConfig | None = None,
        *,
        transport: MesTransport | None = None,
    ) -> None:
        self.cfg = cfg or get_mes_config()
        # rest 모드에서만 transport 필요. table 모드면 lazy.
        self._transport = transport
        if self.cfg.is_rest and self._transport is None:
            self._transport = _build_transport(self.cfg)

    @property
    def transport(self) -> MesTransport | None:
        return self._transport

    # ---- 스테이징(table 모드 + rest 보조 기록) -------------------------------

    def ensure_staged(self, db: Session, row: Inspection, idem_key: str) -> MesQualityIf:
        """mes_quality_if 에 멱등 보장 INSERT. 이미 있으면 기존 행 반환.

        backend 의 POST /inspection(table 모드)이 이미 스테이징했더라도,
        rest 모드로 적재됐거나 로컬 큐 복구분이면 누락될 수 있어 보충한다.

        중복 적재 금지를 위해 idem_key UNIQUE 와 inspection_id 양쪽으로 기존 행을
        탐색한다(타임존 직렬화 차이 등으로 키가 미세하게 달라도 중복을 막는다).
        """
        existing = db.execute(
            select(MesQualityIf).where(
                (MesQualityIf.idem_key == idem_key)
                | (MesQualityIf.inspection_id == row.id)
            )
        ).scalars().first()
        if existing is not None:
            return existing

        staged = MesQualityIf(
            inspection_id=row.id,
            lot=row.lot,
            item_code=row.item_code,
            inspected_at=row.inspected_at,
            cam_id=row.cam_id,
            idem_key=idem_key,
            work_order=row.work_order,
            final_verdict=row.final_verdict,
            defect_codes=list(row.defect_codes or []),
            meas_length_mm=row.meas_length_mm,
            deviation_mm=row.deviation_mm,
        )
        db.add(staged)
        db.flush()
        return staged

    # ---- 단건 연계 -----------------------------------------------------------

    def sync_row(self, db: Session, row: Inspection) -> bool:
        """inspection 한 행을 MES 로 연계. 성공 시 mes_synced=true 로 표시.

        반환 True=연계 성공, False=실패(재시도 큐 유지). 예외는 던지지 않고
        sys_log(category=mes)에 기록한다(워치독이 다음 주기 재시도).
        """
        idem_key = make_idem_key_from_row(row)
        try:
            if self.cfg.is_table:
                self._sync_table(db, row, idem_key)
            else:
                self._sync_rest(db, row, idem_key)
        except MesTransportError as exc:
            db.rollback()
            write_log(
                db,
                category=LogCategory.MES,
                level="ERROR",
                message=f"MES 연계 실패 idem={idem_key}: {exc}",
                payload={"idem_key": idem_key, "mode": self.cfg.mode,
                         "status_code": exc.status_code},
            )
            return False
        except Exception as exc:  # 예기치 못한 오류도 큐 유지
            db.rollback()
            write_log(
                db,
                category=LogCategory.MES,
                level="ERROR",
                message=f"MES 연계 예외 idem={idem_key}: {exc}",
                payload={"idem_key": idem_key, "mode": self.cfg.mode},
            )
            return False

        row.mes_synced = True
        db.add(row)
        db.commit()
        return True

    def _sync_table(self, db: Session, row: Inspection, idem_key: str) -> None:
        """table 모드: 스테이징 보장만 하면 MES 폴링 계약상 연계 완료로 간주."""
        self.ensure_staged(db, row, idem_key)

    def _sync_rest(self, db: Session, row: Inspection, idem_key: str) -> None:
        """rest 모드: 외부 MES 로 POST. 보조로 스테이징 기록도 남긴다."""
        assert self._transport is not None
        payload = _row_to_payload(row, idem_key)
        self._transport.send(payload, idem_key=idem_key)
        # 전송 성공 시 추적용으로 스테이징도 멱등 기록(consumed 표시).
        staged = self.ensure_staged(db, row, idem_key)
        staged.consumed = True


def build_adapter(
    cfg: MesConfig | None = None,
    *,
    transport: MesTransport | None = None,
) -> MesAdapter:
    """현재 설정으로 어댑터 생성(편의 팩토리)."""
    return MesAdapter(cfg, transport=transport)
