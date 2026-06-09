"""월간 품질 리포트 생성 (CLAUDE.md §5 M12, §1.1).

KpiSummary(§1.1 산출식) + 불량유형별 집계 + 일자별 검사/불량 표를
PDF(reportlab) / XLSX(openpyxl) 바이트로 렌더링한다.

한글 라벨을 기본 사용하되, PDF 에서 한글 폰트 등록에 실패하면
라틴 라벨 + 코드 폴백으로 깨짐 없이 출력한다(M12 요구).
"""
from __future__ import annotations

import io
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Optional

from aivis_types import KpiSummary
from db.models import Inspection

# ---- 라벨(한글/라틴 폴백) -------------------------------------------------

# (한글, 라틴 폴백) 쌍. 한글 폰트 미등록 시 라틴 사용.
LABELS: dict[str, tuple[str, str]] = {
    "title": ("AIVIS 월간 품질 리포트", "AIVIS Monthly Quality Report"),
    "period": ("대상 기간", "Period"),
    "generated": ("생성 시각", "Generated"),
    "kpi": ("KPI 요약 (사업계획서 §1.1)", "KPI Summary (sec 1.1)"),
    "total_inspected": ("총 검사수량", "Total inspected"),
    "process_defect_ppm": ("공정불량률 (ppm)", "Process defect (ppm)"),
    "inspection_defect_rate_pct": ("검사불량률 (%)", "Inspection defect (%)"),
    "auto_inspection_rate_pct": ("자동검사율 (%)", "Auto inspection (%)"),
    "storage_mes_rate_pct": ("저장·MES 연계율 (%)", "Storage/MES link (%)"),
    "avg_proc_time_ms": ("평균 처리속도 (ms)", "Avg proc time (ms)"),
    "defect_count": ("공정 불량수량", "Defect count"),
    "defect_breakdown": ("불량유형별 집계", "Defect breakdown"),
    "code": ("코드", "Code"),
    "count": ("건수", "Count"),
    "daily": ("일자별 검사/불량", "Daily inspected / defects"),
    "date": ("일자", "Date"),
    "inspected": ("검사수", "Inspected"),
    "defects": ("불량수", "Defects"),
    "none": ("해당 없음", "None"),
}

# 불량유형 코드 한글 설명(§7.2).
DEFECT_KO = {
    "LEN": "길이",
    "OIL": "유분기",
    "DIS": "변색",
    "SCR": "스크래치",
    "MULTI": "복합",
}


def _lab(key: str, korean_ok: bool) -> str:
    ko, latin = LABELS[key]
    return ko if korean_ok else latin


def _defect_label(code: str, korean_ok: bool) -> str:
    if korean_ok and code in DEFECT_KO:
        return f"{code} ({DEFECT_KO[code]})"
    return code


# ---- 데이터 집계 ----------------------------------------------------------


def aggregate_defects(rows: list[Inspection]) -> list[tuple[str, int]]:
    """defect_codes 배열을 코드별로 집계. 코드 정렬된 (code, count) 리스트."""
    counter: Counter[str] = Counter()
    for r in rows:
        for code in r.defect_codes or []:
            counter[str(code)] += 1
    return sorted(counter.items(), key=lambda x: x[0])


def aggregate_daily(rows: list[Inspection]) -> list[tuple[str, int, int]]:
    """일자(YYYY-MM-DD)별 (검사수, 불량수) 집계. 일자 오름차순."""
    inspected: dict[str, int] = defaultdict(int)
    defects: dict[str, int] = defaultdict(int)
    for r in rows:
        day = r.inspected_at.date().isoformat()
        inspected[day] += 1
        if r.final_verdict == "NG":
            defects[day] += 1
    return [(d, inspected[d], defects.get(d, 0)) for d in sorted(inspected)]


# ---- PDF ------------------------------------------------------------------


def _register_korean_font() -> Optional[str]:
    """가용한 한글 폰트(TTF)를 reportlab 에 등록. 성공 시 폰트명, 실패 시 None.

    CID 폰트(HYSMyeongJo-Medium) 우선 시도 후 시스템 TTF 탐색.
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont

    # 1) reportlab 내장 CID 한글 폰트(추가 파일 불필요).
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("HYSMyeongJo-Medium"))
        return "HYSMyeongJo-Medium"
    except Exception:
        pass

    # 2) 시스템 TTF 폴백.
    candidates = [
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # 한글 X, 등록만
    ]
    for path in candidates:
        if os.path.exists(path) and path.endswith((".ttf",)):
            try:
                pdfmetrics.registerFont(TTFont("AIVIS-KR", path))
                # NanumGothic 만 한글 지원. DejaVu 는 라틴 폴백 유도.
                if "Nanum" in path or "Noto" in path:
                    return "AIVIS-KR"
            except Exception:
                continue
    return None


def render_pdf(summary: KpiSummary, rows: list[Inspection]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    kr_font = _register_korean_font()
    korean_ok = kr_font is not None
    font = kr_font or "Helvetica"
    font_bold = kr_font or "Helvetica-Bold"

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title=f"AIVIS KPI {summary.period}",
    )
    styles = getSampleStyleSheet()
    h1 = styles["Title"]
    h1.fontName = font_bold
    h2 = styles["Heading2"]
    h2.fontName = font_bold
    body = styles["Normal"]
    body.fontName = font

    story = []
    story.append(Paragraph(_lab("title", korean_ok), h1))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    story.append(Paragraph(
        f"{_lab('period', korean_ok)}: {summary.period} &nbsp;&nbsp; "
        f"{_lab('generated', korean_ok)}: {now}", body))
    story.append(Spacer(1, 8 * mm))

    # KPI 요약 표
    story.append(Paragraph(_lab("kpi", korean_ok), h2))
    kpi_rows = [
        [_lab("total_inspected", korean_ok), f"{summary.total_inspected}"],
        [_lab("defect_count", korean_ok), f"{summary.defect_count}"],
        [_lab("process_defect_ppm", korean_ok), f"{summary.process_defect_ppm:.3f}"],
        [_lab("inspection_defect_rate_pct", korean_ok),
         f"{summary.inspection_defect_rate_pct:.3f}"],
        [_lab("auto_inspection_rate_pct", korean_ok),
         f"{summary.auto_inspection_rate_pct:.3f}"],
        [_lab("storage_mes_rate_pct", korean_ok),
         f"{summary.storage_mes_rate_pct:.3f}"],
        [_lab("avg_proc_time_ms", korean_ok),
         ("-" if summary.avg_proc_time_ms is None else f"{summary.avg_proc_time_ms:.2f}")],
    ]
    t = Table(kpi_rows, colWidths=[90 * mm, 70 * mm])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
    ]))
    story.append(t)
    story.append(Spacer(1, 8 * mm))

    # 불량유형별 집계
    story.append(Paragraph(_lab("defect_breakdown", korean_ok), h2))
    breakdown = aggregate_defects(rows)
    if breakdown:
        data = [[_lab("code", korean_ok), _lab("count", korean_ok)]]
        data += [[_defect_label(c, korean_ok), str(n)] for c, n in breakdown]
    else:
        data = [[_lab("code", korean_ok), _lab("count", korean_ok)],
                [_lab("none", korean_ok), "0"]]
    dt = Table(data, colWidths=[90 * mm, 70 * mm])
    dt.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
    ]))
    story.append(dt)
    story.append(Spacer(1, 8 * mm))

    # 일자별 표
    story.append(Paragraph(_lab("daily", korean_ok), h2))
    daily = aggregate_daily(rows)
    ddata = [[_lab("date", korean_ok), _lab("inspected", korean_ok),
              _lab("defects", korean_ok)]]
    if daily:
        ddata += [[d, str(i), str(x)] for d, i, x in daily]
    else:
        ddata += [[_lab("none", korean_ok), "0", "0"]]
    ddt = Table(ddata, colWidths=[70 * mm, 45 * mm, 45 * mm])
    ddt.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))
    story.append(ddt)

    doc.build(story)
    return buf.getvalue()


# ---- XLSX -----------------------------------------------------------------


def render_xlsx(summary: KpiSummary, rows: list[Inspection]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    bold = Font(bold=True)
    head_fill = PatternFill("solid", fgColor="DDDDDD")

    # 시트 1: KPI 요약 (한글 라벨)
    ws = wb.active
    ws.title = "KPI"
    ws["A1"] = LABELS["title"][0]
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = LABELS["period"][0]
    ws["B2"] = summary.period
    ws["A3"] = LABELS["generated"][0]
    ws["B3"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    r = 5
    kpi_pairs = [
        (LABELS["total_inspected"][0], summary.total_inspected),
        (LABELS["defect_count"][0], summary.defect_count),
        (LABELS["process_defect_ppm"][0], summary.process_defect_ppm),
        (LABELS["inspection_defect_rate_pct"][0], summary.inspection_defect_rate_pct),
        (LABELS["auto_inspection_rate_pct"][0], summary.auto_inspection_rate_pct),
        (LABELS["storage_mes_rate_pct"][0], summary.storage_mes_rate_pct),
        (LABELS["avg_proc_time_ms"][0], summary.avg_proc_time_ms),
    ]
    ws.cell(r, 1, LABELS["kpi"][0]).font = bold
    r += 1
    for label, value in kpi_pairs:
        ws.cell(r, 1, label)
        ws.cell(r, 2, value)
        r += 1
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 20

    # 시트 2: 불량유형별 집계 (시트명에 / 등 금지문자 사용 불가 -> 안전한 라틴명)
    ws2 = wb.create_sheet("Defects")
    ws2.cell(1, 1, LABELS["code"][0]).font = bold
    ws2.cell(1, 2, LABELS["count"][0]).font = bold
    ws2["A1"].fill = head_fill
    ws2["B1"].fill = head_fill
    rr = 2
    for code, n in aggregate_defects(rows):
        ws2.cell(rr, 1, _defect_label(code, True))
        ws2.cell(rr, 2, n)
        rr += 1
    ws2.column_dimensions["A"].width = 22

    # 시트 3: 일자별 검사/불량 (시트명은 안전한 라틴명)
    ws3 = wb.create_sheet("Daily")
    for ci, key in enumerate(("date", "inspected", "defects"), start=1):
        c = ws3.cell(1, ci, LABELS[key][0])
        c.font = bold
        c.fill = head_fill
    rr = 2
    for day, ins, dfx in aggregate_daily(rows):
        ws3.cell(rr, 1, day)
        ws3.cell(rr, 2, ins)
        ws3.cell(rr, 3, dfx)
        rr += 1
    ws3.column_dimensions["A"].width = 14

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
