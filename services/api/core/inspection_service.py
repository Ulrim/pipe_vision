"""검사결과 저장 서비스 (CLAUDE.md §5 M7, §7.3 MES).

핵심: 한 트랜잭션 안에서 inspection INSERT + mes_quality_if 스테이징 INSERT 를
함께 커밋해 무결성을 보장한다. 저장 실패 시 호출자가 로컬 큐(local_queue)에
백업하고 재시도해 저장 성공률 100% 를 노린다.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from aivis_types import InspectionResult
from db.models import Inspection, MesQualityIf


def make_idem_key(r: InspectionResult) -> str:
    """MES 멱등키 = lot+item+inspected_at+cam_id (§7.3)."""
    ts = r.inspected_at.isoformat()
    return f"{r.lot}|{r.item_code}|{ts}|{r.cam_id}"


def _verdict_value(v) -> str | None:
    if v is None:
        return None
    return getattr(v, "value", v)


def save_inspection(db: Session, result: InspectionResult, *, mes_mode: str = "table") -> Inspection:
    """검사결과를 트랜잭션으로 저장. table 모드면 MES 스테이징도 동시 INSERT.

    예외 발생 시 호출자가 롤백 후 로컬 큐 백업하도록 예외를 전파한다.
    멱등성: 동일 idem_key 의 mes_quality_if 가 이미 있으면 스테이징은 건너뛴다.
    """
    defect_codes = [getattr(c, "value", c) for c in (result.defect_codes or [])]

    row = Inspection(
        lot=result.lot,
        work_order=result.work_order,
        item_code=result.item_code,
        cam_id=result.cam_id,
        inspected_at=result.inspected_at,
        shift=result.shift,
        operator=result.operator,
        ref_length_mm=result.ref_length_mm,
        meas_length_mm=result.meas_length_mm,
        deviation_mm=result.deviation_mm,
        length_verdict=_verdict_value(result.length_verdict),
        oil_score=result.oil_score,
        discolor_score=result.discolor_score,
        scratch_score=result.scratch_score,
        final_verdict=_verdict_value(result.final_verdict),
        defect_codes=defect_codes,
        confidence=result.confidence,
        raw_image_path=result.raw_image_path,
        result_image_path=result.result_image_path,
        proc_time_ms=result.proc_time_ms,
        review_flag=result.review_flag,
        manual_verdict=_verdict_value(result.manual_verdict),
        mes_synced=result.mes_synced,
    )
    db.add(row)
    db.flush()  # row.id 확보

    if mes_mode == "table":
        idem = make_idem_key(result)
        exists = db.execute(
            select(MesQualityIf.id).where(MesQualityIf.idem_key == idem)
        ).first()
        if not exists:
            db.add(
                MesQualityIf(
                    inspection_id=row.id,
                    lot=result.lot,
                    item_code=result.item_code,
                    inspected_at=result.inspected_at,
                    cam_id=result.cam_id,
                    idem_key=idem,
                    work_order=result.work_order,
                    final_verdict=_verdict_value(result.final_verdict),
                    defect_codes=defect_codes,
                    meas_length_mm=result.meas_length_mm,
                    deviation_mm=result.deviation_mm,
                )
            )

    db.commit()
    db.refresh(row)
    return row
