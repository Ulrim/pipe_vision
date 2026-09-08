"""KPI 산출 라우터 (CLAUDE.md §1.1, §5 M12, §7.4).

산출식은 §1.1 을 그대로 구현한다(임의 변형 금지):
- 공정불량률(ppm)        = (공정 중 불량수량 ÷ 총 검사수량) × 1,000,000
- 검사불량률(%)          = (오검수량 + 미검수량) ÷ 총 검사수량 × 100
- 자동검사율(%)          = AI 자동판정 완료수량 ÷ 총 검사대상수량 × 100
- 데이터 저장&MES 연계율(%) = 정상 저장·연계 건수 ÷ 전체 검사 건수 × 100
- 출하유출불량률(ppm)    = 출하 후 발견된 부적합 ÷ 총 출하수량 × 1,000,000
  (계약 성과지표. 공정불량률과 분자·분모가 모두 달라 환산 불가. 출하 후 정보라
   시스템이 알 수 없으므로 kpi_manual 수기 입력이 있어야 산출된다.)

목표치(인수 기준)는 core/report.kpi_targets() 한 곳에만 둔다. 리포트(PDF/XLSX)와
대시보드 게이지가 서로 다른 목표로 합격을 찍으면 인수 심사에서 숫자가 어긋난다.
GET /kpi/targets 가 그 단일 출처를 화면에 내려준다.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
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

    # 출하유출불량률(ppm) — **계약 성과지표**. 공정불량률과 다른 지표다.
    #   공정불량률 = 공정 중 걸러낸 불량 ÷ 총 검사수량   (시스템이 자동 산출)
    #   출하유출불량률 = 출하 후 발견된 부적합 ÷ 총 출하수량 (수기 입력 필요)
    # 검사에서 걸러낸 불량은 고객에게 가지 않으므로 두 값은 같아질 수 없다.
    # 수기 입력이 없으면 None — 0 으로 채우면 "유출 없음"으로 오독된다.
    shipped_qty = manual.shipped_qty if manual else None
    leak_defect_qty = manual.leak_defect_qty if manual else None
    shipment_leak_ppm: float | None = None
    if shipped_qty and leak_defect_qty is not None:
        shipment_leak_ppm = round(
            _rate(leak_defect_qty, shipped_qty, 1_000_000.0), 3
        )

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
        shipped_qty=shipped_qty,
        leak_defect_qty=leak_defect_qty,
        shipment_leak_ppm=shipment_leak_ppm,
    )
    return summary, rows


@router.get("/targets")
def kpi_targets(
    _user: CurrentUser = Depends(require_min_role(Role.OPERATOR)),
):
    """인수 기준 목표치 목록 — 리포트와 대시보드의 **단일 출처**.

    대시보드가 목표값을 자체 상수로 들고 있으면 env 로 목표를 바꿨을 때
    화면과 리포트가 어긋난다. 그래서 화면도 이 API 를 통해 같은 값을 쓴다.
    """
    out = []
    for key, ko, latin, target_text, rule in report_gen.kpi_targets():
        op, bound = rule.split(":")
        out.append({
            "key": key,
            "label": ko,
            "label_en": latin,
            "target_text": target_text,
            "target_value": float(bound),
            # lte = 낮을수록 좋음(불량률), gte = 높을수록 좋음(달성률)
            "direction": "lower" if op == "lte" else "higher",
        })
    return out


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
    row.shipped_qty = body.shipped_qty
    row.leak_defect_qty = body.leak_defect_qty
    row.note = body.note
    db.commit()
    return KpiManual(
        period=date(key.year, key.month, 1),
        claim_count=row.claim_count,
        workload_index=(float(row.workload_index) if row.workload_index is not None else None),
        lead_time_days=(float(row.lead_time_days) if row.lead_time_days is not None else None),
        shipped_qty=row.shipped_qty,
        leak_defect_qty=row.leak_defect_qty,
        note=row.note,
    )


@router.get("/report/preview")
def kpi_report_preview(
    period: str | None = Query(None, description="대상 월 YYYY-MM (미지정 시 당월)"),
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(require_min_role(Role.OPERATOR)),
):
    """월간 리포트 **미리보기 데이터**(JSON) — 내려받기 전 화면 확인용 (M12).

    PDF/XLSX 와 **완전히 동일한 집계 함수**(report.evaluate_targets /
    proc_time_percentiles / aggregate_defects / aggregate_daily)를 사용한다.
    화면과 파일이 서로 다른 계산을 하면 인수 심사에서 숫자가 어긋나므로,
    단일 산출원을 강제하기 위해 별도 계산을 두지 않는다.

    daily 의 ppm 은 일자별 공정불량률(불량÷검사×1e6)로 추세 차트용이다.
    """
    period = period or _current_period()
    summary, rows = _compute_summary(period, db)

    targets = [
        {
            "key": key,
            "label": ko,
            "label_en": latin,
            "target": target_text,
            "actual": report_gen.target_actual_text(key, summary, rows),
            "achieved": passed,  # True/False/None(판정보류)
        }
        for key, ko, latin, target_text, passed in report_gen.evaluate_targets(
            summary, rows
        )
    ]
    defects = [
        {"code": code, "label": report_gen.defect_label(code), "count": n}
        for code, n in report_gen.aggregate_defects(rows)
    ]
    daily = [
        {
            "date": day,
            "inspected": inspected,
            "defects": defects_n,
            "ppm": (defects_n / inspected * 1_000_000.0) if inspected else 0.0,
        }
        for day, inspected, defects_n in report_gen.aggregate_daily(rows)
    ]
    return {
        "period": summary.period,
        "summary": summary,
        "proc_time": report_gen.proc_time_percentiles(rows),
        "targets": targets,
        "defects": defects,
        "daily": daily,
    }


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
