"""검사결과 적재/조회/이미지/재확인 라우터 (CLAUDE.md §5 M7,M8,M10, §7.4)."""
from __future__ import annotations

import logging
import os
import re
import tempfile
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import Text, cast, select
from sqlalchemy.orm import Session

from aivis_types import (
    InspectionImages,
    InspectionResult,
    LogCategory,
    ReviewUpdate,
    Role,
    Verdict,
)

from core import heartbeat, local_queue
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


class WorkerHost(BaseModel):
    """워커(파이) 자신의 상태 — 하트비트에 실려 온다(2026-10-09, 다중 스테이션).

    API 의 /system/status.system 은 API 가 도는 파이 한 대만 보여준다. 2호기 이상의
    과열·디스크·전원 문제는 이 값으로만 보인다. 모르는 값은 None.
    """

    cpu_temp_c: Optional[float] = None
    cpu_percent: Optional[float] = None
    load_1m: Optional[float] = None
    mem_percent: Optional[float] = None
    disk_percent: Optional[float] = None
    disk_free_gb: Optional[float] = None
    throttled: Optional[bool] = None


class BatchStatus(BaseModel):
    """워커 라이브니스 하트비트(검사결과 아님). 매 검사 사이클 워커가 취득/검출
    상태를 요약해 보내면 API 가 WS 로만 브로드캐스트한다(DB 미기록).

    검출 튜브가 0개이거나 취득이 실패해 POST /inspection 이 0건이어도 HMI 가
    "실시간 연결됨"인 채 아무 이벤트도 못 받아 죽은 것처럼 보이는 문제를 막는다.
    필드는 shared-types(aivis_types)로 내보내지 않고 API 내부 계약으로만 둔다.
    """

    cam_id: str
    item_code: str
    expected: int  # item_master.expected_count(단일검사=1)
    detected: int  # 이번 사이클 검출 튜브 수(0 가능)
    ts: str  # ISO8601
    ng: int = 0  # 이번 사이클 NG 수
    mismatch: bool = False  # detected != expected
    proc_time_ms: int = 0
    stage: Optional[str] = None  # 지금 돌고 있는 검사 모드(InspectionStage 값)
    error: Optional[str] = None  # 취득/검사 오류 요약(정상은 None)
    host: Optional[WorkerHost] = None  # 그 파이 자신의 상태(구 워커는 안 보냄)
    # 단계별 처리시간 ms(2026-10-10): grab/infer/save/total/per_ea/n/post.
    # proc_time_ms 는 이제 취득~저장(§1.2 정의) — 이 분해로 어디가 느린지 본다.
    timings: Optional[dict[str, int]] = None
    # 센서 트리거 대기 중(제품 없음) — 촬영 안 함. '미검출' 과 다르다.
    waiting: bool = False

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


@router.post("/status", status_code=status.HTTP_202_ACCEPTED)
async def broadcast_status(
    body: BatchStatus,
    _internal: None = Depends(require_internal),
):
    """워커 라이브니스 하트비트를 WS /ws/live 로 브로드캐스트만 한다(M6).

    이 이벤트는 검사결과가 아니라 워커가 살아있는지 알리는 하트비트일 뿐이므로
    DB(inspection/sys_log)에 남기지 않는다. 워커가 매 사이클 검출 튜브 수/취득
    오류를 보내면, 검출 0건·취득 실패로 POST /inspection 이 0건인 상황에서도
    HMI 가 상태(mismatch/error)를 실시간으로 받아 "연결됐지만 멈춘 듯한" 오해를
    막는다.

    인증: create_inspection 과 동일한 내부 가드(require_internal). `AIVIS_SERVICE_TOKEN`
    설정 시 X-Service-Token/Bearer 일치 필요, 미설정 시 사내 화이트리스트 허용.

    부수효과: 수신 시각을 core.heartbeat 에 기록해 GET /system/status 가 워커
    생존(up/stale/down)을 판정한다. 기록은 메모리 전용(DB 미기록)이라 고빈도
    하트비트가 로그/테이블을 오염시키지 않는다.
    """
    heartbeat.record(
        body.cam_id,
        stage=body.stage,
        cycle={
            "item_code": body.item_code,
            "expected": body.expected,
            "detected": body.detected,
            "ng": body.ng,
            "mismatch": body.mismatch,
            "proc_time_ms": body.proc_time_ms,
            "error": body.error,
            "timings": body.timings,
            "waiting": body.waiting,
        },
        host=body.host.model_dump() if body.host else None,
    )
    await hub.broadcast(make_event("status", body.model_dump(mode="json")))
    return {"status": "broadcast"}


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
    cam_id: Optional[str] = Query(None, description="스테이션(카메라) ID"),
    stage: Optional[str] = Query(
        None, description="검사 단계(모드): CUT_LENGTH|POST_WASH_SURFACE|CRATE_COUNT"
    ),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    _user: CurrentUser = Depends(require_min_role(Role.OPERATOR)),
):
    """LOT/품목/기간/판정/스테이션/단계 필터 조회 (M8). 서버 페이지네이션.

    스테이션이 2대 이상이면 길이 행과 개수 행이 한 표에 섞인다(2026-10-08).
    cam_id·stage 로 갈라 볼 수 있어야 "어느 스테이션의 NG 인가" 를 읽는다.
    """
    stmt = select(Inspection)
    if lot:
        stmt = stmt.where(Inspection.lot == lot)
    if item:
        stmt = stmt.where(Inspection.item_code == item)
    if verdict:
        stmt = stmt.where(Inspection.final_verdict == verdict.value)
    if cam_id:
        stmt = stmt.where(Inspection.cam_id == cam_id)
    if stage:
        stmt = stmt.where(Inspection.inspection_stage == stage.upper())
    if from_:
        stmt = stmt.where(Inspection.inspected_at >= from_)
    if to:
        stmt = stmt.where(Inspection.inspected_at <= to)
    stmt = stmt.order_by(Inspection.inspected_at.desc()).limit(limit).offset(offset)
    rows = db.execute(stmt).scalars().all()
    return [inspection_to_schema(r) for r in rows]


# ---- 통계 서버 집계 (2026-10-10 점검 보완) ------------------------------------
#
# 대시보드 통계 화면이 행을 5,000건 요청해 브라우저에서 셌는데, 목록 API 상한은
# 2,000건이라 **요청 자체가 422 로 실패**했다(점검 보고서 지적). 상한을 올리는 것은
# 답이 아니다 — 100만 건이면 어떤 상한도 표본이 된다. 그래서 DB 가 센다.


class DefectCount(BaseModel):
    code: str
    count: int


class MonthPoint(BaseModel):
    month: str  # YYYY-MM (KST)
    total: int
    ng: int
    defect_rate_pct: float


class InspectionStats(BaseModel):
    total: int
    ng: int
    by_code: list[DefectCount]
    monthly: list[MonthPoint]


_STAT_CODES = ("LEN", "OIL", "DIS", "SCR", "COUNT", "MULTI")


def _stats_filters(stmt, *, item, from_, to, cam_id, stage):
    if item:
        stmt = stmt.where(Inspection.item_code == item)
    if cam_id:
        stmt = stmt.where(Inspection.cam_id == cam_id)
    if stage:
        stmt = stmt.where(Inspection.inspection_stage == stage.upper())
    if from_:
        stmt = stmt.where(Inspection.inspected_at >= from_)
    if to:
        stmt = stmt.where(Inspection.inspected_at <= to)
    return stmt


@router.get("/stats", response_model=InspectionStats)
def inspection_stats(
    db: Session = Depends(get_db),
    item: Optional[str] = Query(None),
    from_: Optional[datetime] = Query(None, alias="from"),
    to: Optional[datetime] = Query(None),
    cam_id: Optional[str] = Query(None),
    stage: Optional[str] = Query(None),
    _user: CurrentUser = Depends(require_min_role(Role.OPERATOR)),
):
    """불량유형 분포 + 월별(KST) 불량률 — 전 건을 DB 에서 집계(표본 아님).

    by_code: 한 검사에 코드가 여럿이면 각각 센다(MULTI 포함, 화면 규칙과 같다).
    monthly: 한국 시각 기준 월. 행을 내려보내지 않으므로 기간이 길어도 가볍다.
    """
    from sqlalchemy import case, func, literal_column

    kw = dict(item=item, from_=from_, to=to, cam_id=cam_id, stage=stage)
    total, ng = db.execute(
        _stats_filters(
            select(
                func.count(Inspection.id),
                func.sum(case((Inspection.final_verdict == "NG", 1), else_=0)),
            ),
            **kw,
        )
    ).one()
    total, ng = int(total or 0), int(ng or 0)

    dialect = db.get_bind().dialect.name
    by_code: list[DefectCount] = []
    for code in _STAT_CODES:
        if dialect == "postgresql":
            cond = Inspection.defect_codes.any(code)
        else:  # sqlite JSON 배열 텍스트: ["LEN","OIL"]
            cond = func.coalesce(cast(Inspection.defect_codes, Text), "").like(f'%"{code}"%')
        n = db.execute(
            _stats_filters(select(func.count(Inspection.id)).where(cond), **kw)
        ).scalar() or 0
        if n:
            by_code.append(DefectCount(code=code, count=int(n)))

    if dialect == "postgresql":
        month_expr = func.to_char(
            func.timezone("Asia/Seoul", Inspection.inspected_at), "YYYY-MM"
        )
    else:
        month_expr = func.strftime("%Y-%m", Inspection.inspected_at, literal_column("'+9 hours'"))
    rows = db.execute(
        _stats_filters(
            select(
                month_expr.label("m"),
                func.count(Inspection.id),
                func.sum(case((Inspection.final_verdict == "NG", 1), else_=0)),
            ),
            **kw,
        ).group_by("m").order_by("m")
    ).all()
    monthly = [
        MonthPoint(
            month=str(m),
            total=int(t or 0),
            ng=int(n or 0),
            defect_rate_pct=round((int(n or 0) / int(t)) * 100.0, 3) if t else 0.0,
        )
        for m, t, n in rows
        if m
    ]
    return InspectionStats(total=total, ng=ng, by_code=by_code, monthly=monthly)


# ---- LOT 단위 통합 판정 (2026-10-10 점검 보완) ---------------------------------
#
# 점검: "모드 분리 후 '길이+표면 제품단위 통합판정' 이 실제로 안 일어남". 맞다 — 현장
# 요구(2026-10-05)로 스테이션마다 한 가지만 본다. 그리고 다발로 흐르는 튜브는 개별
# 추적이 안 돼 컨베이어 길이 스테이션의 '3번 튜브' 가 세척 뒤 표면 스테이션의 몇 번인지
# 알 수 없다. 그래서 **제품 단위가 아니라 LOT 단위**로 합친다: 그 LOT 이 거친 모든
# 스테이션(모드)의 결과를 모아 하나라도 NG 면 LOT NG, 봐야 할 모드가 아직 없으면 미완.

_STAGE_KO = {
    "CUT_LENGTH": "길이 검사",
    "POST_WASH_SURFACE": "표면 검사",
    "CRATE_COUNT": "개수 확인",
}


class LotStage(BaseModel):
    stage: str
    label: str
    cam_ids: list[str]
    total: int
    ng: int
    ng_rate_pct: float
    by_code: dict[str, int]
    pending_review: int
    first_at: Optional[datetime] = None
    last_at: Optional[datetime] = None


class LotSummary(BaseModel):
    lot: str
    item_codes: list[str]
    final_verdict: str  # OK | NG | INCOMPLETE | NONE
    reasons: list[str]
    required_stages: list[str]
    missing_stages: list[str]
    stages: list[LotStage]


@router.get("/lot-summary", response_model=LotSummary)
def lot_summary(
    lot: str = Query(..., min_length=1),
    require: Optional[str] = Query(
        None,
        description="이 LOT 이 반드시 거쳐야 할 모드(콤마). 비우면 스테이션 설정에 있는 모드 전부",
    ),
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(require_min_role(Role.OPERATOR)),
):
    """LOT 종합 판정 — 스테이션(모드)별 결과를 합친다.

    final_verdict:
      NG          어느 모드에서든 NG 가 1건이라도 있다(사유에 모드별 수치)
      INCOMPLETE  NG 는 없지만 거쳐야 할 모드 중 결과가 없는 것이 있다
      OK          거쳐야 할 모드를 모두 거쳤고 NG 가 없다
      NONE        이 LOT 의 결과가 하나도 없다
    재확인 대기가 남아 있으면 사유에 적는다(판정은 AI 결과 기준).
    """
    from db.models import StationConfig

    rows = db.execute(select(Inspection).where(Inspection.lot == lot)).scalars().all()
    if require:
        required = [x.strip().upper() for x in require.split(",") if x.strip()]
    else:
        required = sorted({
            c.inspection_stage for c in db.execute(select(StationConfig)).scalars().all()
            if c.inspection_stage
        })

    by_stage: dict[str, list[Inspection]] = {}
    for r in rows:
        by_stage.setdefault(r.inspection_stage or "UNSPECIFIED", []).append(r)

    stages: list[LotStage] = []
    reasons: list[str] = []
    any_ng = False
    for st in sorted(by_stage, key=lambda k: list(_STAGE_KO).index(k) if k in _STAGE_KO else 99):
        rs = by_stage[st]
        ng_rows = [r for r in rs if r.final_verdict == "NG"]
        codes: dict[str, int] = {}
        for r in ng_rows:
            for c in r.defect_codes or []:
                codes[str(c)] = codes.get(str(c), 0) + 1
        pending = sum(1 for r in rs if r.review_flag and r.manual_verdict is None)
        times = [r.inspected_at for r in rs if r.inspected_at]
        label = _STAGE_KO.get(st, "모드 미지정" if st == "UNSPECIFIED" else st)
        stages.append(LotStage(
            stage=st, label=label, cam_ids=sorted({r.cam_id for r in rs}),
            total=len(rs), ng=len(ng_rows),
            ng_rate_pct=round(len(ng_rows) / len(rs) * 100.0, 3) if rs else 0.0,
            by_code=dict(sorted(codes.items())), pending_review=pending,
            first_at=min(times) if times else None, last_at=max(times) if times else None,
        ))
        if ng_rows:
            any_ng = True
            unit = "판" if st == "CRATE_COUNT" else "개"
            detail = ", ".join(f"{k} {v}" for k, v in sorted(codes.items()) if k != "MULTI")
            reasons.append(f"{label} NG {len(ng_rows)}{unit} / {len(rs)}{unit}"
                           + (f" ({detail})" if detail else ""))
        if pending:
            reasons.append(f"{label} 재확인 대기 {pending}건")

    missing = [s for s in required if s not in by_stage]
    for m in missing:
        reasons.append(f"{_STAGE_KO.get(m, m)} 결과 없음")
    if not rows:
        verdict = "NONE"
    elif any_ng:
        verdict = "NG"
    elif missing:
        verdict = "INCOMPLETE"
    else:
        verdict = "OK"
    return LotSummary(
        lot=lot, item_codes=sorted({r.item_code for r in rows if r.item_code}),
        final_verdict=verdict, reasons=reasons, required_stages=required,
        missing_stages=missing, stages=stages,
    )


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


# ---- 다중 스테이션: 워커 → 허브 사진 업로드 (2026-10-09) ----------------------
#
# 파이가 여러 대이고 API 는 1호기(허브)에만 있을 때, 2호기의 사진이 2호기 디스크에만
# 남으면 대시보드에서 열 수 없다. 워커가 AIVIS_STORAGE_BACKEND=api 면 이리로 올린다.
# 키는 워커가 DB 에 싣는 상대경로 그대로(raw|result|review/<파일명>.jpg) — 그래서
# 업로드된 파일은 허브 자기 사진과 똑같이 GET /inspection/{id}/images/{kind} 로 열린다.

#: 허용 키. 하위 폴더 금지(보관기한 정리가 바로 아래만 본다), 파일명은 워커의
#: _safe_token 규칙(영숫자 . _ -)과 같다.
_IMAGE_KEY_RE = re.compile(r"^(raw|result|review)/[A-Za-z0-9][A-Za-z0-9._-]{0,200}\.jpg$")
#: 한 장 상한. HQ 카메라 4056x3040 JPEG q95 가 5MB 안팎 — 넉넉히 25MB.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


@router.put("/images/{key:path}", status_code=status.HTTP_201_CREATED)
async def upload_inspection_image(
    key: str,
    request: Request,
    _internal: None = Depends(require_internal),
):
    """워커가 찍은 JPEG 를 허브 images_dir 의 같은 키 경로에 저장(내부 전용).

    - 인증: POST /inspection 과 같은 내부 가드(require_internal).
    - 키 검증: `raw|result|review/<파일명>.jpg` 만. `..`·하위 폴더·다른 확장자는 400.
    - 본문은 JPEG(FF D8 로 시작)만. 크기 상한 25MB(413).
    - 같은 키를 다시 올리면 덮어쓴다(스풀 재전송이 같은 사진을 또 보낼 수 있다 — 멱등).
    - 임시파일에 쓴 뒤 rename — 반쯤 쓴 파일을 대시보드가 여는 일이 없다.
    - API 가 supabase 백엔드면 409: 그 구성에선 워커가 Supabase 에 직접 올린다.
    """
    settings = get_settings()
    if settings.storage_backend == "supabase":
        raise HTTPException(
            status_code=409,
            detail="이 서버는 Supabase 저장 구성입니다 — 워커를 AIVIS_STORAGE_BACKEND=supabase 로",
        )
    if not _IMAGE_KEY_RE.match(key) or ".." in key:
        raise HTTPException(status_code=400, detail="허용되지 않는 이미지 키")
    body = await request.body()
    if len(body) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="이미지가 너무 큽니다")
    if len(body) < 4 or body[:2] != b"\xff\xd8":
        raise HTTPException(status_code=400, detail="JPEG 가 아닙니다")
    abs_path = _safe_image_path(key)
    if not abs_path:
        raise HTTPException(status_code=400, detail="허용되지 않는 이미지 키")
    folder = os.path.dirname(abs_path)
    try:
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".upload-", suffix=".part")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(body)
            os.replace(tmp, abs_path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError as exc:
        # 디스크 가득 등 — 워커가 스풀에 보존하고 다시 보낸다(사진 유실 없음).
        log.error("이미지 업로드 저장 실패 %s: %s", key, exc)
        raise HTTPException(status_code=507, detail="허브 디스크에 저장하지 못했습니다")
    return {"key": key, "bytes": len(body)}


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
