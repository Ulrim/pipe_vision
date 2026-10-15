"""MES 연계 REST 엔드포인트 (CLAUDE.md §7.3, §7.4 POST /mes/quality).

data-mes 에이전트 소유(services/api/mes/*)의 본격 워치독/재전송 큐는 별도지만,
여기서는 REST 모드 수신 엔드포인트 + 멱등 스테이징을 제공한다.
멱등키 = lot+item+inspected_at+cam_id (중복 적재 방지).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from aivis_types import InspectionResult, LogCategory

from core.inspection_service import make_idem_key
from core.logging import write_log
from core.security import require_internal
from db.base import get_db
from db.models import MesQualityIf

router = APIRouter(prefix="/mes", tags=["mes"])


@router.post("/quality", dependencies=[Depends(require_internal)])
def mes_quality(body: InspectionResult, db: Session = Depends(get_db)):
    """REST 모드 MES 연계 수신. 멱등키로 중복 적재 방지.

    내부 가드(2026-10-15): 종전엔 인증이 전혀 없어, 공개된 클라우드 API 에서는 누구나 연계
    표에 행을 넣고 MES 가 그것을 품질이력으로 가져갈 수 있었다. POST /inspection 과 같은
    서비스 토큰을 요구한다(토큰 미설정 사내 구성은 종전대로).
    """
    idem = make_idem_key(body)
    exists = db.execute(
        select(MesQualityIf).where(MesQualityIf.idem_key == idem)
    ).scalar_one_or_none()
    if exists:
        return {"status": "duplicate", "idem_key": idem, "id": exists.id}

    defect_codes = [getattr(c, "value", c) for c in (body.defect_codes or [])]
    row = MesQualityIf(
        inspection_id=body.id,
        lot=body.lot,
        item_code=body.item_code,
        inspected_at=body.inspected_at,
        cam_id=body.cam_id,
        idem_key=idem,
        work_order=body.work_order,
        final_verdict=getattr(body.final_verdict, "value", body.final_verdict),
        defect_codes=defect_codes,
        meas_length_mm=body.meas_length_mm,
        deviation_mm=body.deviation_mm,
    )
    db.add(row)
    write_log(db, category=LogCategory.MES, message=f"mes.rest staged {idem}", commit=False)
    db.commit()
    db.refresh(row)
    return {"status": "staged", "idem_key": idem, "id": row.id}
