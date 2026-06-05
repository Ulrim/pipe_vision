"""KPI 산출 라우터 (CLAUDE.md §1.1, §5 M12, §7.4).

산출식은 §1.1 을 그대로 구현한다(임의 변형 금지):
- 공정불량률(ppm)        = (공정 중 불량수량 ÷ 총 검사수량) × 1,000,000
- 검사불량률(%)          = (오검수량 + 미검수량) ÷ 총 검사수량 × 100
- 자동검사율(%)          = AI 자동판정 완료수량 ÷ 총 검사대상수량 × 100
- 데이터 저장&MES 연계율(%) = 정상 저장·연계 건수 ÷ 전체 검사 건수 × 100
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aivis_types import KpiManual, KpiSummary, Role

from core import report as report_gen
from core.security import CurrentUser, require_min_role
from db.base import get_db
from db.models import Inspection, KpiManual as KpiManualRow

router = APIRouter(prefix="/kpi", tags=["kpi"])


def _month_bounds(period: str) -> tuple[datetime, datetime]:
    """'YYYY-MM' -> [월초, 다음달초) 경계(UTC, tz-aware)."""
    try:
        year, month = (int(x) for x in period.split("-")[:2])
        start = datetime(year, month, 1, tzinfo=timezone.utc)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="period 형식은 YYYY-MM")
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    return start, end


def _rate(numer: int, denom: int, factor: float) -> float:
    """분모 0 보호. denom==0 이면 0.0."""
    if denom <= 0:
        return 0.0
    return (numer / denom) * factor


def _current_period() -> str:
    """당월 'YYYY-MM' (UTC)."""
    now = datetime.now(timezone.utc)
    return f"{now.year:04d}-{now.month:02d}"


def _compute_summary(period: str, db: Session) -> tuple[KpiSummary, list[Inspection]]:
    """§1.1 산출식으로 월별 KPI 요약 + 대상 inspection 행을 함께 반환.

    KpiSummary 응답 엔드포인트와 리포트 생성기가 공유하는 단일 산출 경로
    (산출식 일관성 보장)."""
    start, end = _month_bounds(period)
    base = select(Inspection).where(
        Inspection.inspected_at >= start, Inspection.inspected_at < end
    )
    rows = list(db.execute(base).scalars().all())

    total_inspected = len(rows)  # 총 검사수량

    # 공정 중 불량수량 = final_verdict == 'NG'
    defect_count = sum(1 for r in rows if r.final_verdict == "NG")
    process_defect_ppm = _rate(defect_count, total_inspected, 1_000_000.0)

    # 자동검사율: AI 자동판정 완료수량(final_verdict 존재) ÷ 총 검사대상수량
    auto_inspected = sum(1 for r in rows if r.final_verdict)
    auto_inspection_rate_pct = _rate(auto_inspected, total_inspected, 100.0)

    # 검사불량률: (오검 + 미검) ÷ 총 검사수량 × 100
    #  - 오검(misjudge): 작업자 재확인(manual_verdict)이 입력됐고 AI 판정과 불일치
    #  - 미검(miss): 재확인 대상(review_flag=True)인데 manual_verdict 미입력
    misjudge_count = sum(
        1
        for r in rows
        if r.manual_verdict is not None and r.manual_verdict != r.final_verdict
    )
    miss_count = sum(
        1 for r in rows if r.review_flag and r.manual_verdict is None
    )
    inspection_defect_rate_pct = _rate(
        misjudge_count + miss_count, total_inspected, 100.0
    )

    # 데이터 저장&MES 연계율: 정상 저장·연계 건수 ÷ 전체 검사 건수
    #  - 저장 건수 = DB 에 적재된 행(=total_inspected, 조회된 rows)
    #  - 연계 건수 = mes_synced True
    stored_count = total_inspected
    mes_synced_count = sum(1 for r in rows if r.mes_synced)
    storage_mes_rate_pct = _rate(mes_synced_count, stored_count, 100.0)

    proc_times = [r.proc_time_ms for r in rows if r.proc_time_ms is not None]
    avg_proc_time_ms = (sum(proc_times) / len(proc_times)) if proc_times else None

    # 수기 KPI(있으면 함께 노출): 해당 월 1일 키.
    manual = db.get(KpiManualRow, datetime(start.year, start.month, 1))

    summary = KpiSummary(
        period=f"{start.year:04d}-{start.month:02d}",
        total_inspected=total_inspected,
        defect_count=defect_count,
        process_defect_ppm=round(process_defect_ppm, 3),
        auto_inspected=auto_inspected,
        auto_inspection_rate_pct=round(auto_inspection_rate_pct, 3),
        misjudge_count=misjudge_count,
        miss_count=miss_count,
        inspection_defect_rate_pct=round(inspection_defect_rate_pct, 3),
        stored_count=stored_count,
        mes_synced_count=mes_synced_count,
        storage_mes_rate_pct=round(storage_mes_rate_pct, 3),
        avg_proc_time_ms=(round(avg_proc_time_ms, 2) if avg_proc_time_ms is not None else None),
        claim_count=manual.claim_count if manual else None,
        workload_index=(float(manual.workload_index) if manual and manual.workload_index is not None else None),
        lead_time_days=(float(manual.lead_time_days) if manual and manual.lead_time_days is not None else None),
    )
    return summary, rows


@router.get("/summary", response_model=KpiSummary)
def kpi_summary(
    period: str = Query(..., description="대상 월 YYYY-MM"),
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(require_min_role(Role.OPERATOR)),
):
    """월별 KPI 자동 산출 (§1.1). 로그인 필요(operator+)."""
    summary, _rows = _compute_summary(period, db)
    return summary


@router.post("/manual", response_model=KpiManual, status_code=status.HTTP_200_OK)
def upsert_kpi_manual(
    body: KpiManual,
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(require_min_role(Role.QUALITY)),
):
    """작업공수/리드타임/Claim 등 비자동 KPI 입력(월 단위 upsert)."""
    # period 를 월 1일로 정규화.
    key = datetime(body.period.year, body.period.month, 1)
    row = db.get(KpiManualRow, key)
    if not row:
        row = KpiManualRow(period=key)
        db.add(row)
    row.claim_count = body.claim_count
    row.workload_index = body.workload_index
    row.lead_time_days = body.lead_time_days
    row.note = body.note
    db.commit()
    return KpiManual(
        period=date(key.year, key.month, 1),
        claim_count=row.claim_count,
        workload_index=(float(row.workload_index) if row.workload_index is not None else None),
        lead_time_days=(float(row.lead_time_days) if row.lead_time_days is not None else None),
        note=row.note,
    )


_MEDIA_TYPES = {
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


@router.get("/report")
def kpi_report(
    period: str | None = Query(None, description="대상 월 YYYY-MM (미지정 시 당월)"),
    fmt: str = Query("pdf", pattern="^(pdf|xlsx)$"),
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(require_min_role(Role.QUALITY)),
):
    """월간 품질 리포트 생성 (M12 DoD).

    §1.1 KPI(공정불량률 ppm / 검사불량률 % / 자동검사율 / 저장·연계율) +
    불량유형별 집계 + 일자별 검사/불량 표를 PDF(reportlab) / XLSX(openpyxl)
    파일로 생성해 첨부 다운로드로 반환한다. period 미지정 시 당월.
    """
    period = period or _current_period()
    summary, rows = _compute_summary(period, db)

    if fmt == "pdf":
        content = report_gen.render_pdf(summary, rows)
    else:
        content = report_gen.render_xlsx(summary, rows)

    filename = f"aivis_kpi_{summary.period}.{fmt}"
    return StreamingResponse(
        iter([content]),
        media_type=_MEDIA_TYPES[fmt],
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(content)),
        },
    )
