"""검사결과 적재/조회/이미지/재확인 라우터 (CLAUDE.md §5 M7,M8,M10, §7.4)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from aivis_types import (
    InspectionImages,
    InspectionResult,
    LogCategory,
    ReviewUpdate,
    Role,
    Verdict,
)

from core import local_queue
from core.config import get_settings
from core.inspection_service import save_inspection
from core.logging import write_log
from core.security import (
    CurrentUser,
    require_internal,
    require_min_role,
    require_role,
)
from db.base import SessionLocal, get_db
from db.models import Inspection
from db.serialize import inspection_to_schema
from ws.alarm import tracker
from ws.hub import hub, make_event

router = APIRouter(prefix="/inspection", tags=["inspection"])


def _save_with_backup(result: InspectionResult) -> Optional[InspectionResult]:
    """DB 저장 시도. 실패 시 로컬 큐 백업(M7 DoD). 성공 시 저장 결과 반환.

    네트워크/DB 일시 장애에도 검사결과 유실을 막아 저장 성공률 100% 를 노린다.
    """
    settings = get_settings()
    db = SessionLocal()
    try:
        row = save_inspection(db, result, mes_mode=settings.mes_mode)
        saved = inspection_to_schema(row)
        # 저장 성공 로그(M15: db 카테고리).
        write_log(
            db,
            category=LogCategory.DB,
            message=f"inspection.store ok id={row.id} lot={row.lot} verdict={row.final_verdict}",
        )
        return saved
    except Exception as exc:
        db.rollback()
        # 저장 실패 로그(M15: error 카테고리). 로그 적재 자체도 실패하면 무시.
        try:
            write_log(
                db,
                category=LogCategory.ERROR,
                level="ERROR",
                message=f"inspection.store fail lot={result.lot}: {exc}",
            )
        except Exception:
            db.rollback()
        # 로컬 큐 백업 후 None (호출자가 status=queued 로 응답).
        local_queue.backup(result)
        return None
    finally:
        db.close()


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_inspection(
    result: InspectionResult,
    _internal: None = Depends(require_internal),
):
    """검사워커가 결과를 적재(서버 내부 호출). 저장 실패 시 로컬 큐 백업.

    인증: 내부용 엔드포인트. `AIVIS_SERVICE_TOKEN` 설정 시 X-Service-Token/Bearer
    일치 필요, 미설정 시 사내 화이트리스트 허용(§4 단일 호스트 토폴로지).

    저장 성공 시 WS /ws/live 로 검사결과(+NG 알람)를 브로드캐스트한다(M6).
    cam_id 단위 연속 NG 가 임계(AIVIS_CONSEC_NG_THRESHOLD, 기본 3) 이상이면
    consecutive_ng 알람을 추가 브로드캐스트한다.
    """
    saved = _save_with_backup(result)
    if saved is None:
        # 백업됨 — 워치독이 재처리. 데이터는 유실되지 않음.
        return {
            "status": "queued",
            "detail": "DB 저장 실패: 로컬 큐 백업됨, 재시도 예정",
            "pending": local_queue.pending_count(),
        }

    # 실시간 푸시(연결된 HMI 없으면 no-op).
    payload = saved.model_dump(mode="json")
    await hub.broadcast(make_event("inspection", payload))

    is_ng = saved.final_verdict in (Verdict.NG.value, "NG")
    # cam_id 단위 연속 NG 카운터 갱신(OK 수신 시 0 리셋).
    consec = tracker.record(saved.cam_id, "NG" if is_ng else "OK")

    if is_ng:
        # 단일 NG 알람(기존 동작 유지).
        await hub.broadcast(
            make_event(
                "alarm",
                {
                    "kind": "ng",
                    "id": saved.id,
                    "lot": saved.lot,
                    "cam_id": saved.cam_id,
                    "defect_codes": payload.get("defect_codes"),
                },
            )
        )
        # 연속 NG 임계 도달 시 추가 알람(M6).
        threshold = get_settings().consec_ng_threshold
        if consec >= threshold:
            await hub.broadcast(
                make_event(
                    "alarm",
                    {
                        "kind": "consecutive_ng",
                        "cam_id": saved.cam_id,
                        "count": consec,
                        "threshold": threshold,
                    },
                )
            )

    return {"status": "stored", "id": saved.id, "inspection": saved}


@router.post("/retry-queue")
def retry_local_queue(
    _user: CurrentUser = Depends(require_role(Role.QUALITY, Role.ADMIN)),
):
    """로컬 큐에 백업된 검사결과를 재저장(워치독/수동 트리거)."""
    settings = get_settings()

    def saver(r: InspectionResult) -> None:
        db = SessionLocal()
        try:
            save_inspection(db, r, mes_mode=settings.mes_mode)
        finally:
            db.close()

    drained = local_queue.drain(saver)
    return {"drained": drained, "pending": local_queue.pending_count()}


@router.get("", response_model=list[InspectionResult])
def list_inspections(
    db: Session = Depends(get_db),
    lot: Optional[str] = Query(None),
    item: Optional[str] = Query(None, description="item_code"),
    verdict: Optional[Verdict] = Query(None, description="final_verdict OK/NG"),
    from_: Optional[datetime] = Query(None, alias="from"),
    to: Optional[datetime] = Query(None),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    _user: CurrentUser = Depends(require_min_role(Role.OPERATOR)),
):
    """LOT/품목/기간/판정 필터 조회 (M8). 서버 페이지네이션. 로그인 필요(operator+)."""
    stmt = select(Inspection)
    if lot:
        stmt = stmt.where(Inspection.lot == lot)
    if item:
        stmt = stmt.where(Inspection.item_code == item)
    if verdict:
        stmt = stmt.where(Inspection.final_verdict == verdict.value)
    if from_:
        stmt = stmt.where(Inspection.inspected_at >= from_)
    if to:
        stmt = stmt.where(Inspection.inspected_at <= to)
    stmt = stmt.order_by(Inspection.inspected_at.desc()).limit(limit).offset(offset)
    rows = db.execute(stmt).scalars().all()
    return [inspection_to_schema(r) for r in rows]


@router.get("/{insp_id}", response_model=InspectionResult)
def get_inspection(
    insp_id: int,
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(require_min_role(Role.OPERATOR)),
):
    row = db.get(Inspection, insp_id)
    if not row:
        raise HTTPException(status_code=404, detail="검사결과 없음")
    return inspection_to_schema(row)


@router.get("/{insp_id}/images", response_model=InspectionImages)
def get_inspection_images(
    insp_id: int,
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(require_min_role(Role.OPERATOR)),
):
    """원본/결과 이미지 경로 (M8). 로그인 필요(operator+)."""
    row = db.get(Inspection, insp_id)
    if not row:
        raise HTTPException(status_code=404, detail="검사결과 없음")
    return InspectionImages(
        id=row.id,
        raw_image_path=row.raw_image_path,
        result_image_path=row.result_image_path,
    )


@router.patch("/{insp_id}/review", response_model=InspectionResult)
def review_inspection(
    insp_id: int,
    body: ReviewUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(
        require_role(Role.OPERATOR, Role.QUALITY, Role.ADMIN)
    ),
):
    """NG 제품 재확인 결과 입력 (M10). 작업자 이상 권한."""
    row = db.get(Inspection, insp_id)
    if not row:
        raise HTTPException(status_code=404, detail="검사결과 없음")
    row.manual_verdict = (
        body.manual_verdict.value
        if isinstance(body.manual_verdict, Verdict)
        else body.manual_verdict
    )
    # review_flag 미지정 시 처리 완료로 해제.
    row.review_flag = bool(body.review_flag) if body.review_flag is not None else False
    if body.operator:
        row.operator = body.operator
    write_log(
        db,
        category=LogCategory.USER,
        message=f"review insp={insp_id} manual={row.manual_verdict} by={user.username}",
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return inspection_to_schema(row)
