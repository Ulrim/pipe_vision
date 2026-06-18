# -*- coding: utf-8 -*-
"""AIVIS 하드웨어·소프트웨어 정리 + 실 장비 구매·개발 계획안 PDF 생성."""
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer, Table,
    TableStyle, PageBreak, HRFlowable,
)

SERIF = "HYSMyeongJo-Medium"
GOTHIC = "HYGothic-Medium"
pdfmetrics.registerFont(UnicodeCIDFont(SERIF))
pdfmetrics.registerFont(UnicodeCIDFont(GOTHIC))

NAVY = colors.HexColor("#1f3a5f")
STEEL = colors.HexColor("#2b6cb0")
LIGHT = colors.HexColor("#eef3f9")
GREY = colors.HexColor("#666666")
LINE = colors.HexColor("#c9d6e5")

ss = getSampleStyleSheet()
def P(name, **kw):
    base = dict(fontName=SERIF, fontSize=9.5, leading=14, textColor=colors.black)
    base.update(kw)
    return ParagraphStyle(name, **base)

st_title = P("title", fontName=GOTHIC, fontSize=22, leading=28, textColor=NAVY, alignment=TA_CENTER)
st_sub = P("sub", fontName=SERIF, fontSize=11, leading=16, textColor=GREY, alignment=TA_CENTER)
st_h1 = P("h1", fontName=GOTHIC, fontSize=14, leading=20, textColor=NAVY, spaceBefore=10, spaceAfter=6)
st_h2 = P("h2", fontName=GOTHIC, fontSize=11.5, leading=16, textColor=STEEL, spaceBefore=8, spaceAfter=4)
st_body = P("body", fontSize=9.5, leading=14.5, spaceAfter=4)
st_small = P("small", fontSize=8.3, leading=12, textColor=GREY)
st_cell = P("cell", fontSize=8.6, leading=12)
st_cellb = P("cellb", fontName=GOTHIC, fontSize=8.6, leading=12, textColor=colors.white)
st_cellc = P("cellc", fontSize=8.6, leading=12, alignment=TA_CENTER)
st_note = P("note", fontSize=8.6, leading=13, textColor=GREY)

def cell(t, c=st_cell):
    return Paragraph(t, c)

def header_row(cells):
    return [Paragraph(c, st_cellb) for c in cells]

def mk_table(data, widths, align_center_cols=()):
    t = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]
    t.setStyle(TableStyle(style))
    return t

flow = []
def add(*x): flow.extend(x)
def sp(h=6): flow.append(Spacer(1, h))
def rule(): flow.append(HRFlowable(width="100%", thickness=0.6, color=LINE, spaceBefore=4, spaceAfter=6))

# ───────────────────────── 표지 ─────────────────────────
add(Spacer(1, 60*mm))
add(Paragraph("AIVIS", P("brand", fontName=GOTHIC, fontSize=30, leading=34, textColor=STEEL, alignment=TA_CENTER)))
sp(4)
add(Paragraph("AI 머신비전 품질검사 시스템", st_title))
sp(6)
add(Paragraph("하드웨어·소프트웨어 정리 &amp; 실 장비 구매·개발 계획안", st_sub))
sp(30)
meta = [
    ["도입기업", "유한회사 에이엠피 (Clad AL Header Pipe 제조, 전남 광양)"],
    ["공급기업", "레피소드㈜ (AI 솔루션 개발)"],
    ["사업", "2026년 지역주도형 AI대전환사업 — AI솔루션 구축지원"],
    ["문서 작성일", "2026-06-18"],
    ["문서 버전", "v1.0"],
]
t = Table([[Paragraph(a, st_cell), Paragraph(b, st_cell)] for a, b in meta], colWidths=[35*mm, 120*mm])
t.setStyle(TableStyle([
    ("GRID", (0,0), (-1,-1), 0.4, LINE),
    ("BACKGROUND", (0,0), (0,-1), LIGHT),
    ("FONTNAME", (0,0), (0,-1), GOTHIC),
    ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
]))
add(t)
sp(20)
add(Paragraph("본 문서는 시스템의 하드웨어/소프트웨어 경계를 정리하고, 현장 도입을 위해 "
              "도입기업이 구매해야 할 실 장비(BOM·추정 견적)와 잔여 개발·통합 일정을 제시한다. "
              "소프트웨어 및 AI 추론 엔진은 시뮬레이터 기준 개발이 완료되었으며, 남은 작업은 "
              "실 장비 설치 후의 통합·데이터 수집·모델 고도화 단계다.", st_note))
add(PageBreak())

# ───────────────────────── 1. 경계 원칙 ─────────────────────────
add(Paragraph("1. 스코프 경계 원칙 (HW / SW)", st_h1))
add(Paragraph("모든 소프트웨어는 <b>하드웨어 추상화 계층(HAL)</b> 뒤에서 동작한다. 덕분에 실물 카메라 없이 "
              "시뮬레이터(AIVIS_CAMERA=sim)로 전 파이프라인을 검증했고, 현장에서는 <b>어댑터 결선만</b> 바꾸면 된다. "
              "하드웨어(카메라·조명·트리거·산업용 PC·MES 본체)는 도입기업이 구축(스코프 외)하고, 소프트웨어 전체와 "
              "이들과 붙는 <b>연동 어댑터/인터페이스</b>는 공급기업이 개발(스코프 내)한다.", st_body))
sp(2)
data = [header_row(["하드웨어 (도입기업 · 스코프 외)", "→ 접점 (SW 제공)", "소프트웨어 (공급기업 · 스코프 내)"])]
data += [
    [cell("카메라 / 렌즈 / 조명"), cell("CameraAdapter · GenICam 어댑터", st_cellc), cell("AI 추론 엔진(검사 파이프라인)")],
    [cell("트리거 센서 (디지털 IO)"), cell("TriggerSource (IO/MQTT)", st_cellc), cell("백엔드 API · DB · KPI")],
    [cell("산업용 PC (GPU)"), cell("Docker 런타임 호스트", st_cellc), cell("작업자 HMI · 관리자 대시보드")],
    [cell("경광등 / 부저"), cell("알람 신호 발신 I/F", st_cellc), cell("이미지/검사이력 저장")],
    [cell("MES 솔루션 본체"), cell("MES 연계 I/F (table/REST)", st_cellc), cell("데이터·재학습 운영도구")],
]
add(mk_table(data, [55*mm, 52*mm, 55*mm]))
sp(8)

# ───────────────────────── 2. 소프트웨어 현황 ─────────────────────────
add(Paragraph("2. 소프트웨어 개발 현황 (스코프 내 · 완료)", st_h1))
data = [header_row(["레이어 / 서비스", "내용", "상태"])]
rows = [
    ("AI 추론 (services/vision)", "취득(HAL)→전처리(ROI)→길이(서브픽셀 CV)→표면(유분기/변색/스크래치)→종합판정, 검사 워커", "완료(sim)"),
    ("백엔드 (services/api)", "FastAPI, 검사결과 저장(트랜잭션·로컬큐), KPI 산출, RBAC/JWT, WebSocket, 로그, 이미지 서빙", "완료"),
    ("데이터/MES", "MES 연계 어댑터(table/REST·멱등·워치독), 라벨링·오검미검 재학습셋 빌드", "완료"),
    ("작업자 HMI (apps/hmi)", "실시간 검사화면(WS), NG 알람, 재확인 입력, 전체 로그인", "완료"),
    ("관리자 대시보드 (apps/dashboard)", "LOT 이력·불량통계·월별추이·KPI 게이지·PDF/XLSX 리포트", "완료"),
    ("저장소", "PostgreSQL(메타/KPI) · 이미지(로컬 또는 Supabase Storage)", "완료"),
    ("인프라/배포", "docker-compose(현장 단일PC)·오프라인 패키지·CI·클라우드 배포(Vercel/Render/Supabase)", "완료"),
]
for a, b, c in rows:
    data.append([cell("<b>%s</b>" % a), cell(b), cell(c, st_cellc)])
add(mk_table(data, [45*mm, 95*mm, 22*mm]))
sp(3)
add(Paragraph("검증: 200+ 자동화 테스트 그린(vision 113 · api 71 · data-ops 16 · HMI 33 · dashboard 36), "
              "FAT/SAT 하니스 — 자동검사율 100%, 처리속도 ~10ms(목표 300ms), 저장·연계율 100%.", st_note))
add(PageBreak())

# ───────────────────────── 3. HW↔SW 접점 ─────────────────────────
add(Paragraph("3. 하드웨어 ↔ 소프트웨어 접점 (잔여 결선)", st_h1))
data = [header_row(["하드웨어", "소프트웨어 접점", "현재 상태 / 남은 작업"])]
rows = [
    ("산업용 카메라", "CameraAdapter → GenICamCamera (harvesters/pypylon)", "인터페이스·레시피 매핑 완성 → 실카메라 SDK 결선(P7)"),
    ("트리거 센서", "TriggerSource → DigitalIO / MQTT", "골격 완성 → 센서 결선(P7), 현재 타이머/파일워처로 대체"),
    ("경광등·부저", "알람 이벤트(WebSocket) → IO 출력", "신호 발신까지 완료 → 물리 결선은 HW"),
    ("MES 본체", "MES 연계 어댑터(스테이징 테이블/REST)", "내부 완결 → 실 MES URL/테이블 결선"),
    ("표면 결함 모델", "ONNX 런타임 분기", "고전 CV 폴백 동작 → 실데이터 축적 후 모델 학습·교체"),
]
for a, b, c in rows:
    data.append([cell("<b>%s</b>" % a), cell(b), cell(c)])
add(mk_table(data, [32*mm, 62*mm, 68*mm]))
sp(8)

# ───────────────────────── 4. 실 장비 구매 계획 ─────────────────────────
add(Paragraph("4. 실 장비 구매 계획 (BOM · 추정 견적)", st_h1))
add(Paragraph("소구경 박육 알루미늄 튜브(Header Pipe) 1개 단위 검사 스테이션 기준. <b>길이</b>는 텔레센트릭 렌즈+"
              "백라이트(실루엣)로 서브픽셀 정밀 계측, <b>변색·유분기</b>는 컬러+확산광, <b>스크래치</b>는 저각도 사광이 핵심이다. "
              "아래는 1차 스테이션 권장 구성과 추정 단가이며, <b>실제 금액은 제품 치수·택트타임·조명 환경에 따라 벤더 견적이 필요</b>하다. "
              "(총 H/W 예산 42백만원 기준 배분안)", st_body))
sp(2)
data = [header_row(["#", "항목", "권장 사양 / 예시", "수량", "추정(백만원)"])]
bom = [
    ("1", "머신비전 카메라(측면/길이·스크래치)", "Mono 12MP GigE Vision (Basler/HIKROBOT 등)", "1", "4.0"),
    ("2", "머신비전 카메라(단면/변색·유분기)", "Color 5MP GigE Vision", "1", "3.0"),
    ("3", "텔레센트릭 렌즈(길이 계측)", "Bi-telecentric (Computar/Opto Eng. 등)", "1", "3.5"),
    ("4", "표준 FA 렌즈(표면)", "고해상 FA 렌즈", "1", "1.0"),
    ("5", "백라이트(투과조명·길이 실루엣)", "콜리메이트 LED 백라이트", "1", "1.5"),
    ("6", "확산조명(돔/바·색/유분기/변색)", "돔 또는 바 확산광", "1", "2.0"),
    ("7", "저각도 사광(스크래치 raking)", "라인/바 조명", "1", "1.5"),
    ("8", "조명 컨트롤러(스트로브/디밍)", "다채널 트리거 컨트롤러", "1", "1.5"),
    ("9", "산업용 PC(GPU 탑재)", "GPU(RTX급) + 산업용 본체(Advantech 등)", "1", "6.0"),
    ("10", "HMI 터치 패널/모니터", "15~21\" 산업용 터치", "1", "1.5"),
    ("11", "포토 트리거 센서 + 앰프", "제품 도달 감지", "1", "0.5"),
    ("12", "디지털 IO 모듈", "트리거 입력 / 알람 출력", "1", "0.8"),
    ("13", "경광등(3색)+부저", "타워등 + 부저", "1", "0.4"),
    ("14", "산업용 GigE 스위치", "기가비트 산업용 스위치", "1", "0.5"),
    ("15", "치구(V홈 받침·정렬)+차광 후드/암실", "제품 정렬·조명 차폐", "1식", "3.0"),
    ("16", "캘리브레이션 타깃(스케일 기준자)", "px-mm 환산용 게이지", "1", "0.5"),
    ("17", "카메라/조명 마운팅·브래킷·케이블", "프레임·배선 일체", "1식", "2.0"),
    ("18", "설치·배선·시운전(노무)", "현장 설치·정렬·시운전", "1식", "4.0"),
    ("19", "예비비(약 10%)", "환율/사양 변동 대비", "-", "3.6"),
]
for r in bom:
    data.append([cell(r[0], st_cellc), cell("<b>%s</b>" % r[1]), cell(r[2]), cell(r[3], st_cellc), cell(r[4], st_cellc)])
data.append([Paragraph("합계 (추정)", st_cellb), "", "", "", Paragraph("≈ 42.3", st_cellb)])
t = mk_table(data, [8*mm, 50*mm, 60*mm, 14*mm, 22*mm])
t.setStyle(TableStyle([
    ("BACKGROUND", (0,0), (-1,0), NAVY),
    ("GRID",(0,0),(-1,-1),0.4,LINE),
    ("ROWBACKGROUNDS",(0,1),(-1,-2),[colors.white, LIGHT]),
    ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
    ("TOPPADDING",(0,0),(-1,-1),3.5),("BOTTOMPADDING",(0,0),(-1,-1),3.5),
    ("LEFTPADDING",(0,0),(-1,-1),5),("RIGHTPADDING",(0,0),(-1,-1),5),
    ("BACKGROUND",(0,-1),(-1,-1),NAVY),
    ("SPAN",(0,-1),(3,-1)),
    ("ALIGN",(0,-1),(3,-1),"RIGHT"),
]))
add(t)
sp(3)
add(Paragraph("※ 벤더 예시는 참고용이며 권고가 아니다. 카메라 해상도·렌즈 배율은 제품 전장/공차와 검출 최소 결함 크기(스크래치 폭)로 "
              "역산해 확정한다. 변색은 컬러(필요 시 다분광) 권장. 조명·치구는 금속 곡면 반사를 고정하는 것이 정확도의 핵심이므로 "
              "차광 후드/암실을 반드시 포함한다.", st_note))
add(PageBreak())

# ───────────────────────── 5. 개발(통합) 계획안 ─────────────────────────
add(Paragraph("5. 개발·통합 계획안 (장비 입고 후, 약 12~16주)", st_h1))
add(Paragraph("소프트웨어는 완료 상태이므로 잔여 일정은 <b>실 장비 통합 → 데이터 수집 → 모델 고도화 → FAT/SAT → 시범운영</b> "
              "이다. 각 단계는 산출물로 종료를 판정한다.", st_body))
sp(2)
data = [header_row(["단계", "주요 활동", "산출물", "기간", "주관"])]
plan = [
    ("R1 광학·카메라 통합", "장비 설치/정렬, 차광 후드, GenICam 어댑터 결선, 촬영 레시피(노출/게인/조명) 확정", "촬영 셋업 + sim→실카메라 전환", "2주", "공급+도입"),
    ("R2 캘리브레이션·데이터수집", "스케일 기준자로 px-mm 환산, 부록A 규격 촬영(OK/LEN/OIL/DIS/SCR/MULTI/경계)", "캘리브레이션값 + 정답셋", "2~3주", "도입(촬영)+공급"),
    ("R3 표면모델 학습·임계보정", "PyTorch 학습→ONNX export, 품목별 임계 보정, 고전CV→모델 교체", "표면 모델 + 정확도≥95%", "2~3주", "공급"),
    ("R4 MES 연계 결선", "실 MES URL/인터페이스 테이블 결선, 멱등·재시도 검증", "MES 연계율 100%", "1주", "공급+도입IT"),
    ("R5 FAT(공장수락)", "샘플 기반 기능·성능 4지표 자동검증, 트리거/알람/저장/UI 점검", "FAT 결과서", "1주", "공급+도입"),
    ("R6 SAT·시범운영", "실생산품 전수, 오검·미검 환류, 임계 재보정, MSA 반복성", "SAT·MSA 결과서", "3~4주", "도입+공급"),
    ("R7 안정화·인수", "운영 교육, 매뉴얼 최종화, 백업/모니터링, 최종 인수", "인수 산출물 일체", "1~2주", "공급+도입"),
]
for a, b, c, d, e in plan:
    data.append([cell("<b>%s</b>" % a), cell(b), cell(c), cell(d, st_cellc), cell(e, st_cellc)])
add(mk_table(data, [33*mm, 60*mm, 38*mm, 13*mm, 18*mm]))
sp(6)

add(Paragraph("간이 일정 (주차)", st_h2))
weeks = ["1","2","3","4","5","6","7","8","9","10","12","14","16"]
gantt_rows = [
    ("R1", [1,1,0,0,0,0,0,0,0,0,0,0,0]),
    ("R2", [0,1,1,1,0,0,0,0,0,0,0,0,0]),
    ("R3", [0,0,0,1,1,1,0,0,0,0,0,0,0]),
    ("R4", [0,0,0,0,0,1,1,0,0,0,0,0,0]),
    ("R5", [0,0,0,0,0,0,1,1,0,0,0,0,0]),
    ("R6", [0,0,0,0,0,0,0,1,1,1,1,0,0]),
    ("R7", [0,0,0,0,0,0,0,0,0,0,1,1,1]),
]
gd = [[Paragraph("단계", st_cellb)] + [Paragraph(w, st_cellb) for w in weeks]]
for name, marks in gantt_rows:
    gd.append([Paragraph("<b>%s</b>" % name, st_cell)] + ["" for _ in weeks])
gt = Table(gd, colWidths=[16*mm] + [11*mm]*len(weeks))
gstyle = [
    ("BACKGROUND",(0,0),(-1,0),NAVY),
    ("GRID",(0,0),(-1,-1),0.4,LINE),
    ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
    ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
]
for ri, (_, marks) in enumerate(gantt_rows, start=1):
    for ci, m in enumerate(marks, start=1):
        if m:
            gstyle.append(("BACKGROUND", (ci, ri), (ci, ri), STEEL))
gt.setStyle(TableStyle(gstyle))
add(gt)
sp(8)

# ───────────────────────── 6. 리스크 & 체크리스트 ─────────────────────────
add(Paragraph("6. 리스크 대응 & 구매 전 체크리스트", st_h1))
data = [header_row(["리스크", "대응"])]
rk = [
    ("초기 학습데이터 부족 → 표면 정확도", "고전 CV 폴백으로 동작 보장 후 점진 학습, 라벨링 보조툴·경계샘플 조기 확보"),
    ("금속 반사·조명 편차", "차광 후드/암실 + 품목별 촬영 레시피 + 치구·조명 고정"),
    ("길이 정확도(서브픽셀)", "텔레센트릭 렌즈 + 백라이트(실루엣) + 스케일 기준자 캘리브레이션"),
    ("처리속도 300ms 초과", "ONNX/INT8 양자화, ROI 축소, GPU 프로파일(이미 sim에서 ~10ms)"),
    ("실카메라 통합 지연", "HAL로 sim 선행 개발 완료 → GenICam 결선만 남김"),
]
for a, b in rk:
    data.append([cell("<b>%s</b>" % a), cell(b)])
add(mk_table(data, [55*mm, 107*mm]))
sp(6)
add(Paragraph("구매 전 확정 필요 항목", st_h2))
for t in [
    "제품 전장(mm)·허용 공차, 검출 최소 결함 크기(스크래치 폭/깊이) → 카메라 해상도·렌즈 배율 역산",
    "택트타임(개/분) → 셔터·조명 스트로브·처리 예산",
    "검사 구도 확정: 측면(길이+OD) / 단면(버·내면 변색) — 카메라 대수 결정",
    "설치 위치(절단–디버링–세척 구간)와 제품 자세(singulation 여부)",
    "MES 연계 방식(인터페이스 테이블 vs REST)과 담당 IT 채널",
]:
    add(Paragraph("• " + t, st_body))

# footer page numbers
def on_page(canvas, doc):
    canvas.saveState()
    canvas.setFont(SERIF, 8)
    canvas.setFillColor(GREY)
    canvas.drawString(20*mm, 12*mm, "AIVIS — HW/SW 정리 및 장비구매·개발 계획안")
    canvas.drawRightString(190*mm, 12*mm, "p. %d" % doc.page)
    canvas.setStrokeColor(LINE)
    canvas.line(20*mm, 15*mm, 190*mm, 15*mm)
    canvas.restoreState()

OUT = "/home/user/pipe_vision/docs/AIVIS_장비구매_개발계획안.pdf"
doc = BaseDocTemplate(OUT, pagesize=A4,
                      leftMargin=20*mm, rightMargin=20*mm, topMargin=18*mm, bottomMargin=20*mm)
frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
doc.addPageTemplates([PageTemplate(id="t", frames=[frame], onPage=on_page)])
doc.build(flow)
print("WROTE", OUT)
