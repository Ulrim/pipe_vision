"""검사결과 적재/조회/이미지/재확인 라우터 (CLAUDE.md §5 M7,M8,M10, §7.4)."""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.responses import FileResponse, StreamingResponse
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

# write_log(DB 기반 sys_log)와 local_queue(파일) 는 둘 다 같은 디스크에 쓴다.
# 디스크 동시 소진 등으로 두 경로가 함께 실패하면(§M7 DoD 위반: 검사결과 완전
# 유실) DB/파일에 의존하지 않는 표준 로거로 최소한의 흔적을 남긴다.
log = logging.getLogger("aivis.api.inspection")


def _save_with_backup(
    result: InspectionResult,
) -> Optional[tuple[InspectionResult, bool]]:
    """DB 저장 시도. 실패 시 로컬 큐 백업(M7 DoD). 성공 시 (저장 결과, created).

    created=False 는 자연키 중복(엣지 스풀 재전송 등)으로 기존 행을 반환한 경우.
    네트워크/DB 일시 장애에도 검사결과 유실을 막아 저장 성공률 100% 를 노린다.
    """
    settings = get_settings()
    db = SessionLocal()
    try:
        row, created = save_inspection(db, result, mes_mode=settings.mes_mode)
        saved = inspection_to_schema(row)
        # 저장 성공 로그(M15: db 카테고리). 중복(dedup)도 구분해 기록.
        outcome = "ok" if created else "dedup"
        write_log(
            db,
            category=LogCategory.DB,
            message=f"inspection.store {outcome} id={row.id} lot={row.lot} verdict={row.final_verdict}",
        )
        return saved, created
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
        try:
            local_queue.backup(result)
        except Exception as backup_exc:
            # DB 저장도, 로컬 큐 백업도 실패 — 검사결과가 어디에도 남지 않는다.
            # 여기서 조용히 None 을 반환하면 호출자가 status=queued(200) 로
            # 응답해 엣지 워커가 "성공"으로 오인하고 자기 스풀에도 적재하지
            # 않는다(이중 유실). 표준 로거(파일/DB 미의존)로 흔적을 남긴 뒤
            # 예외를 그대로 전파해 5xx 가 나가게 하고, 엣지 워커의 오프라인
            # 스풀이 최후의 방어선으로 재시도하게 한다.
            log.critical(
                "category=error inspection.store 완전 유실(DB+로컬백업 모두 실패) "
                "lot=%s item_code=%s cam_id=%s inspected_at=%s: %s",
                result.lot,
                result.item_code,
                result.cam_id,
                result.inspected_at,
                backup_exc,
            )
            raise
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

    멱등성: 자연키 (lot, item_code, inspected_at, cam_id) 가 동일한 재전송
    (엣지 오프라인 스풀 — 서버는 저장했으나 응답 유실)은 행을 새로 만들지 않고
    기존 행 id 로 동일 스키마 응답한다. 이때 WS 브로드캐스트/연속 NG 카운트는
    발화하지 않는다(이중 알람 방지).
    """
    outcome = _save_with_backup(result)
    if outcome is None:
        # 백업됨 — 워치독이 재처리. 데이터는 유실되지 않음.
        return {
            "status": "queued",
            "detail": "DB 저장 실패: 로컬 큐 백업됨, 재시도 예정",
            "pending": local_queue.pending_count(),
        }

    saved, created = outcome
    if not created:
        # 자연키 중복(재전송) — 기존 행 반환. 브로드캐스트/알람/카운터 미발화.
        return {"status": "stored", "id": saved.id, "inspection": saved}

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


def _safe_image_path(rel: str) -> str:
    """images_dir 기준 상대경로 rel 을 안전하게 절대경로로 해석.

    경로 traversal 방지(M8 보안): realpath(join) 가 realpath(images_dir) 하위가
    아니면 None 취급(상위 노출 차단). rel 에 `..` 나 절대경로가 섞여도 escape 불가.
    반환: 검증 통과한 절대경로. 검증 실패 시 빈 문자열.
    """
    images_dir = get_settings().images_dir
    base = os.path.realpath(images_dir)
    target = os.path.realpath(os.path.join(base, rel))
    # base 자체이거나 base 하위여야 함. os.sep 접두 검사로 형제 디렉터리(prefix) 오탐 방지.
    if target != base and not target.startswith(base + os.sep):
        return ""
    return target


@router.get("/{insp_id}/images/{kind}")
def get_inspection_image_bytes(
    insp_id: int,
    kind: str = Path(..., pattern="^(raw|result)$"),
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(require_min_role(Role.OPERATOR)),
):
    """원본/결과 이미지 **바이트 스트리밍** (M8). 로그인 필요(operator+).

    kind = raw|result. 비전 워커가 공유 볼륨(AIVIS_IMAGES_DIR) 하위에 저장한
    JPEG 를 인증 하에 반환한다. 경로는 DB 의 상대경로를 traversal 안전하게 해석한다.
    경로 미지정/파일 부재/escape 시도 시 404.
    """
    row = db.get(Inspection, insp_id)
    if not row:
        raise HTTPException(status_code=404, detail="검사결과 없음")

    rel = row.raw_image_path if kind == "raw" else row.result_image_path
    if not rel:
        raise HTTPException(status_code=404, detail=f"{kind} 이미지 경로 없음")

    settings = get_settings()
    if settings.storage_backend == "supabase":
        return _serve_supabase(rel, kind, settings)

    # backend == local (기본): 공유 볼륨 FileResponse + traversal 가드.
    abs_path = _safe_image_path(rel)
    if not abs_path or not os.path.isfile(abs_path):
        # escape 시도/파일 부재를 동일하게 404 처리(경로 존재 여부 정보 노출 회피).
        raise HTTPException(status_code=404, detail=f"{kind} 이미지 파일 없음")

    return FileResponse(
        abs_path,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=86400"},
    )


def _serve_supabase(rel: str, kind: str, settings) -> StreamingResponse:
    """Supabase Storage 오브젝트를 JWT 가드 뒤에서 프록시 스트리밍 (M8).

    DB 상대경로(raw/...|result/...)를 오브젝트 키로 사용해 service_role 키로
    인증된 GET 을 수행한다. 공개 리다이렉트 대신 바이트를 직접 프록시하여
    이미지 접근에 우리 JWT 가드를 강제한다. 미설정/404/타임아웃/예외는 404.
    """
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise HTTPException(status_code=404, detail=f"{kind} 이미지 스토리지 미설정")

    base = settings.supabase_url.rstrip("/")
    key = rel.lstrip("/")
    url = f"{base}/storage/v1/object/{settings.supabase_storage_bucket}/{key}"
    headers = {
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
        "apikey": settings.supabase_service_role_key,
    }
    try:
        resp = httpx.get(url, headers=headers, timeout=10.0)
    except httpx.HTTPError:
        raise HTTPException(status_code=404, detail=f"{kind} 이미지 조회 실패")

    if resp.status_code != 200:
        raise HTTPException(status_code=404, detail=f"{kind} 이미지 파일 없음")

    return StreamingResponse(
        iter([resp.content]),
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=86400"},
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
