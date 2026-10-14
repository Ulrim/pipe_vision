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

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aivis_types import LogCategory

from core.logging import write_log
from db.models import Inspection, MesQualityIf
from mes.config import MesConfig, get_mes_config
from mes.transport import (
    FakeMesTransport,
    HttpxMesTransport,
    MesTransport,
    MesTransportError,
    UnconfiguredMesTransport,
)


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


def staging_values(row: Inspection) -> dict[str, Any]:
    """연계로 넘기는 값(검사 행 기준). 테이블 적재·보충·REST 본문이 모두 이것을 쓴다.

    2026-10-14: 단계·튜브 번호·재확인 결과·크레이트 개수를 더했다 — MES 가 연계값만으로
    생산실적(단계별 불량, 재확인 반영 양품, 포장 수량)을 셀 수 있어야 한다.
    """
    return {
        "inspection_id": row.id,
        "lot": row.lot,
        "item_code": row.item_code,
        "inspected_at": row.inspected_at,
        "cam_id": row.cam_id,
        "work_order": row.work_order,
        "final_verdict": row.final_verdict,
        "defect_codes": list(row.defect_codes or []),
        "meas_length_mm": row.meas_length_mm,
        "deviation_mm": row.deviation_mm,
        "inspection_stage": row.inspection_stage,
        "tube_index": row.tube_index,
        "manual_verdict": row.manual_verdict,
        "count_detected": row.count_detected,
        "count_expected": row.count_expected,
    }


def _row_to_payload(row: Inspection, idem_key: str) -> dict[str, Any]:
    """MES REST 본문(§7.3 식별자+판정 핵심값, JSON 직렬화)."""
    out = staging_values(row)
    ts = out["inspected_at"]
    out["inspected_at"] = ts.isoformat() if isinstance(ts, datetime) else str(ts)
    for k in ("meas_length_mm", "deviation_mm"):
        out[k] = float(out[k]) if out[k] is not None else None
    out["idem_key"] = idem_key
    return out


def restage_after_review(db: Session, row: Inspection, cfg: MesConfig | None = None) -> None:
    """재확인 결과가 바뀌면 MES 가 다시 가져가게 한다(2026-10-14).

    MES 생산실적의 양품 수량이 재확인으로 바뀌기 때문이다(AI NG → 사람 OK 면 양품).
    - table: 연계 행의 manual_verdict 를 고치고 consumed=false·updated_at 갱신 → MES 가
      다음 폴링에 같은 멱등키로 다시 읽어 갱신한다.
    - rest: mes_synced=false 로 되돌려 워치독이 재확인 값을 실어 다시 보낸다.
    수집용 LOT 은 처음부터 MES 로 가지 않으므로 건드리지 않는다. commit 은 호출자.
    """
    from core.training import is_training_lot

    if is_training_lot(row.lot):
        return
    cfg = cfg or get_mes_config()
    staged = db.execute(
        select(MesQualityIf).where(MesQualityIf.inspection_id == row.id)
    ).scalars().first()
    if staged is not None and staged.manual_verdict != row.manual_verdict:
        staged.manual_verdict = row.manual_verdict
        staged.consumed = False
        staged.consumed_at = None
        staged.updated_at = datetime.now(timezone.utc)
    if cfg.is_rest and row.mes_synced:
        row.mes_synced = False


def _build_transport(cfg: MesConfig) -> MesTransport:
    """rest 모드 전송 객체 생성.

    URL 있음 → 실제 전송. URL 없음 → 연계 보류(UnconfiguredMesTransport).
    가짜 전송(FakeMesTransport)은 MES_REST_FAKE=true 로 명시했을 때만 — 시험·시연용.
    """
    if cfg.rest_url:
        return HttpxMesTransport(
            cfg.rest_url,
            timeout_s=cfg.rest_timeout_s,
            idem_header=cfg.idem_header,
            token=cfg.rest_token,
        )
    if cfg.rest_fake:
        return FakeMesTransport()
    return UnconfiguredMesTransport()


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
        #: 설정으로 만든 가짜 전송인가(MES_REST_FAKE). 주입된 전송은 실제로 본다.
        self._fake = False
        if self.cfg.is_rest and self._transport is None:
            self._transport = _build_transport(self.cfg)
            self._fake = isinstance(self._transport, FakeMesTransport)

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

        staged = MesQualityIf(**staging_values(row), idem_key=idem_key)
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
        # 전송 성공 시 추적용으로 스테이징도 멱등 기록. consumed 는 **실제 MES 가
        # 받았을 때만** — 가짜 전송은 MES 가 받은 것이 아니다.
        staged = self.ensure_staged(db, row, idem_key)
        if not self._fake:
            staged.consumed = True


def build_adapter(
    cfg: MesConfig | None = None,
    *,
    transport: MesTransport | None = None,
) -> MesAdapter:
    """현재 설정으로 어댑터 생성(편의 팩토리)."""
    return MesAdapter(cfg, transport=transport)
