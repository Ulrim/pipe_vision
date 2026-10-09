"""인쇄용 보정 타깃(체커보드·기준자) PDF 생성 — 치수가 정확해야 한다.

왜 PDF 를 직접 쓰는가:
  * **벡터**라 해상도에 무관하고, 길이를 밀리미터로 못 박을 수 있다. PNG 로
    내보내면 DPI 해석에 따라 인쇄 크기가 달라진다.
  * 외부 라이브러리를 쓰지 않는다. 현장 파이는 인터넷이 막혀 있을 수 있고,
    이 도구는 보정 직전에 돌아야 한다.

PDF 단위는 **포인트**이고 1pt = 1/72 inch 로 정의돼 있다. 따라서
`pt = mm × 72 / 25.4` 가 근사가 아니라 정의다. A4 = 210×297mm 을 그대로 환산해
MediaBox 에 넣으므로, 뷰어·프린터가 배율을 건드리지 않는 한 실제 치수가 나온다.

── 인쇄 정확도에 대한 경고 (이게 핵심이다) ──────────────────────────────

프린터는 보통 **±0.2~0.5% 의 배율 오차**를 낸다. 250mm 기준이면 0.5~1.25mm 다.
±0.1mm 공차에 비하면 터무니없이 크다. 그래서 둘을 다르게 다뤄야 한다.

* **체커보드 — 인쇄 오차가 거의 문제되지 않는다.** 렌즈 왜곡 계수(k1,k2…)는
  무차원이고 보드의 *모양*에서 나온다. 칸 크기는 외부 파라미터의 스케일만
  정하는데, 우리는 거기서 길이를 읽지 않는다. 균일하게 0.3% 작게 인쇄돼도
  왜곡 추정은 그대로다. (단, 종이가 **휘면** 모양이 깨지므로 치명적이다 —
  반드시 평평한 판에 붙일 것.)

* **기준자 — 인쇄 오차가 곧바로 측정 오차다.** 눈금 간격이 길이 환산의
  기준이기 때문이다. 해법은 "정확히 인쇄하기" 가 아니라 **"인쇄된 실제 값을
  재서 그 값을 쓰기"** 다. 시트에 측정용 기준선과 기입란을 함께 찍는 이유다.
  최선은 금속/유리 눈금자를 사는 것이고, 인쇄물은 그 전까지의 대용이다.

사용:
    python -m vision.tools.make_targets checkerboard --out board.pdf
    python -m vision.tools.make_targets gauge --out gauge.pdf --pitch-mm 10
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence, Tuple

#: 1 pt = 1/72 inch (PDF 정의). 근사가 아니다.
MM_PER_INCH = 25.4
PT_PER_INCH = 72.0

#: 용지 크기(mm). ISO 216 정의값.
PAGES = {
    "a4": (210.0, 297.0),
    "a3": (297.0, 420.0),
    "letter": (215.9, 279.4),
}


def mm2pt(mm: float) -> float:
    return mm * PT_PER_INCH / MM_PER_INCH


@dataclass(frozen=True)
class Rect:
    """mm 단위. 원점은 **왼쪽 아래**(PDF 좌표계)."""

    x: float
    y: float
    w: float
    h: float


@dataclass(frozen=True)
class Circle:
    cx: float
    cy: float
    r: float


@dataclass(frozen=True)
class Text:
    x: float
    y: float
    s: str
    size_pt: float = 9.0

    def __post_init__(self) -> None:
        # 기본 14종 폰트(Helvetica)만 쓰므로 한글은 렌더되지 않는다. 시트의
        # 문구를 영문·숫자로 제한하는 이유다 — 한글 설명은 문서에 둔다.
        # 폰트를 임베드하면 한글도 되지만, 의존성 없이 가려는 이 모듈의
        # 목적과 맞바꿀 만한 이득이 아니다.
        if any(ord(c) > 0x7E for c in self.s):
            raise ValueError(f"시트 문구는 ASCII 여야 한다(폰트 미임베드): {self.s!r}")


Shape = Rect | Circle


@dataclass(frozen=True)
class Sheet:
    """한 장의 기하. PDF 로도 쓰고 테스트에서 래스터화도 한다 —
    **같은 기하를 쓰므로** 테스트가 실제 산출물을 검증한다."""

    page_w_mm: float
    page_h_mm: float
    shapes: List[Shape]
    texts: List[Text]
    title: str


# ---------------------------------------------------------------- PDF 쓰기

def _content_stream(sheet: Sheet) -> str:
    """모든 도형을 검정으로 채운다. 좌표는 pt."""
    out: List[str] = ["0 0 0 rg"]
    for sh in sheet.shapes:
        if isinstance(sh, Rect):
            out.append(
                f"{mm2pt(sh.x):.4f} {mm2pt(sh.y):.4f} "
                f"{mm2pt(sh.w):.4f} {mm2pt(sh.h):.4f} re f"
            )
        else:
            # 베지어 4개로 원 근사. 매직넘버 0.5522847498 = 4/3·(√2−1).
            k = 0.5522847498307933
            cx, cy, r = mm2pt(sh.cx), mm2pt(sh.cy), mm2pt(sh.r)
            o = r * k
            out.append(f"{cx + r:.4f} {cy:.4f} m")
            out.append(f"{cx + r:.4f} {cy + o:.4f} {cx + o:.4f} {cy + r:.4f} "
                       f"{cx:.4f} {cy + r:.4f} c")
            out.append(f"{cx - o:.4f} {cy + r:.4f} {cx - r:.4f} {cy + o:.4f} "
                       f"{cx - r:.4f} {cy:.4f} c")
            out.append(f"{cx - r:.4f} {cy - o:.4f} {cx - o:.4f} {cy - r:.4f} "
                       f"{cx:.4f} {cy - r:.4f} c")
            out.append(f"{cx + o:.4f} {cy - r:.4f} {cx + r:.4f} {cy - o:.4f} "
                       f"{cx + r:.4f} {cy:.4f} c")
            out.append("f")
    for t in sheet.texts:
        esc = t.s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        out.append(
            f"BT /F1 {t.size_pt:.2f} Tf "
            f"{mm2pt(t.x):.4f} {mm2pt(t.y):.4f} Td ({esc}) Tj ET"
        )
    return "\n".join(out)


def to_pdf(sheet: Sheet) -> bytes:
    """최소 PDF 1.4. xref 오프셋을 정확히 써야 뷰어가 연다."""
    content = _content_stream(sheet).encode("ascii")
    w, h = mm2pt(sheet.page_w_mm), mm2pt(sheet.page_h_mm)
    title = sheet.title.replace("(", r"\(").replace(")", r"\)")

    objs: List[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w:.4f} {h:.4f}] "
         f"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>").encode(),
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n"
        + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Title ({title}) /Producer (AIVIS make_targets) >>".encode(),
    ]

    buf = bytearray(b"%PDF-1.4\n")
    offsets: List[int] = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(buf))
        buf += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_at = len(buf)
    buf += f"xref\n0 {len(objs) + 1}\n".encode()
    buf += b"0000000000 65535 f \n"
    for off in offsets:
        buf += f"{off:010d} 00000 n \n".encode()
    buf += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info 6 0 R >>\n"
            f"startxref\n{xref_at}\n%%EOF\n").encode()
    return bytes(buf)


# ------------------------------------------------------------- 공통 요소

#: 인쇄 배율 확인용 기준선 길이(mm). 길수록 배율 오차를 잘 드러낸다.
VERIFY_LINE_MM = 200.0
#: 기준선·눈금의 선 두께(mm).
TICK_W = 0.3
#: 배율 확인선 눈금의 높이(mm). 기준자의 캘리퍼 눈금(8mm)과 **다르게** 둔다 —
#: 같으면 도형만 보고 둘을 구분할 수 없다.
VERIFY_TICK_H = 10.0
#: 마크 행 둘레의 정숙 구역(mm). 현장에서 ROI 를 마크 행에 맞춰 잡을 때 글자나
#: 눈금이 함께 들어오면 **연결성분이 늘어 간격 적합이 깨진다.** 실제로 그 일이
#: 나서(캘리퍼 눈금이 ROI 에 걸려 잔차 18px) 이 상수를 뒀다.
QUIET_MM = 15.0


def _verify_ruler(x: float, y: float, length_mm: float = VERIFY_LINE_MM
                  ) -> Tuple[List[Shape], List[Text]]:
    """배율 확인용 기준선. **이 시트에서 가장 중요한 요소다.**

    프린터가 0.3% 줄여 인쇄해도 눈으로는 모른다. 알려진 길이의 선을 함께
    찍어두면 자로 한 번 재서 바로 안다.
    """
    shapes: List[Shape] = [
        Rect(x, y - TICK_W / 2, length_mm, TICK_W),          # 본선
        Rect(x - TICK_W / 2, y - VERIFY_TICK_H / 2, TICK_W, VERIFY_TICK_H),
        Rect(x + length_mm - TICK_W / 2, y - VERIFY_TICK_H / 2,
             TICK_W, VERIFY_TICK_H),
    ]
    texts = [
        Text(x, y + 6.0,
             f"VERIFY SCALE: outer tick-to-tick = {length_mm:.2f} mm", 10.0),
        Text(x, y - 11.0,
             "If it measures different, your printer scaled the page. "
             "Reprint at 100% (Actual size, no fit-to-page).", 7.5),
    ]
    return shapes, texts


# ------------------------------------------------------------- 체커보드

def checkerboard_sheet(
    *, cols: int = 9, rows: int = 6, square_mm: float = 20.0,
    page: str = "a4", landscape: bool = True,
) -> Sheet:
    """OpenCV findChessboardCorners 용 보드.

    cols/rows 는 **내부 코너 수**다. 칸 수는 (cols+1)×(rows+1).
    9×6(홀수×짝수)이 기본인 이유는 회전 모호성이 없어서다 — 짝수×짝수면
    180° 돌린 것과 구분되지 않아 코너 순서가 뒤집힐 수 있다.
    """
    if cols < 2 or rows < 2:
        raise ValueError("내부 코너는 각 방향 2개 이상")
    if cols % 2 == rows % 2:
        raise ValueError(
            f"cols({cols})·rows({rows}) 의 홀짝이 같으면 회전이 모호해진다. "
            "하나는 홀수, 하나는 짝수로 하라(예: 9x6)."
        )
    pw, ph = PAGES[page]
    if landscape:
        pw, ph = ph, pw

    nx, ny = cols + 1, rows + 1
    bw, bh = nx * square_mm, ny * square_mm
    # 아래쪽에 안내문·기준선 자리를 남긴다.
    footer = 34.0
    if bw > pw or bh > ph - footer:
        raise ValueError(
            f"보드 {bw:.0f}x{bh:.0f}mm 가 {page}"
            f"{'(가로)' if landscape else ''} {pw:.0f}x{ph:.0f}mm 에 안 들어간다. "
            "--square-mm 을 줄이거나 --page a3 를 쓰라."
        )

    x0 = (pw - bw) / 2
    y0 = footer + (ph - footer - bh) / 2

    shapes: List[Shape] = []
    for iy in range(ny):
        for ix in range(nx):
            if (ix + iy) % 2 == 0:          # 좌하단 칸이 검정
                shapes.append(
                    Rect(x0 + ix * square_mm, y0 + iy * square_mm,
                         square_mm, square_mm)
                )

    vr_len = min(VERIFY_LINE_MM, pw - 40.0)
    vs, vt = _verify_ruler((pw - vr_len) / 2, 20.0, vr_len)
    shapes += vs
    texts = vt + [
        Text(x0, y0 + bh + 4.0,
             f"AIVIS lens calibration target - {cols}x{rows} inner corners, "
             f"{square_mm:g} mm squares", 9.0),
        Text(x0, 30.0,
             f"calibrate_lens --cols {cols} --rows {rows} "
             f"--square-mm {square_mm:g}", 8.0),
    ]
    return Sheet(pw, ph, shapes, texts,
                 f"AIVIS checkerboard {cols}x{rows} {square_mm:g}mm")


# -------------------------------------------------------------- 기준자

def gauge_sheet(
    *, pitch_mm: float = 10.0, marks: int = 0, mark_d_mm: float = 0.0,
    page: str = "a4", landscape: bool = True,
) -> Sheet:
    """프레임별 배율용 기준자(등간격 원형 마크).

    marks=0 이면 용지에 들어가는 최대 개수를 쓴다. 마크가 많을수록 등간격
    적합이 좋아지므로(1/√N) 기본은 '가득'이다.
    """
    if pitch_mm <= 0:
        raise ValueError("pitch_mm 은 0 보다 커야 한다")
    pw, ph = PAGES[page]
    if landscape:
        pw, ph = ph, pw

    margin = 18.0
    usable = pw - 2 * margin
    max_marks = int(usable // pitch_mm) + 1
    if marks <= 0:
        marks = max_marks
    if marks > max_marks:
        raise ValueError(
            f"마크 {marks}개 × {pitch_mm:g}mm 는 {page} 에 안 들어간다"
            f"(최대 {max_marks}개). --page a3 또는 간격을 줄이라."
        )
    if marks < 3:
        raise ValueError("마크는 3개 이상이어야 간격을 적합할 수 있다")

    # 마크가 서로 붙으면 연결성분이 하나로 합쳐져 검출이 깨진다.
    if mark_d_mm <= 0:
        mark_d_mm = pitch_mm * 0.5
    if mark_d_mm >= pitch_mm * 0.8:
        raise ValueError(
            f"마크 지름 {mark_d_mm:g}mm 가 간격 {pitch_mm:g}mm 에 비해 크다 "
            "— 이웃끼리 붙어 검출이 깨진다(간격의 80% 미만)."
        )

    span = (marks - 1) * pitch_mm
    x0 = (pw - span) / 2
    cy = ph - 42.0

    shapes: List[Shape] = [
        Circle(x0 + i * pitch_mm, cy, mark_d_mm / 2) for i in range(marks)
    ]
    # 첫/마지막 마크 중심에 캘리퍼용 눈금을 세운다. 사람이 재는 기준은
    # '원의 가장자리'가 아니라 '중심'이어야 코드가 보는 값과 같다.
    # 마크 행에서 QUIET_MM 만큼 떨어뜨린다 — 가까우면 현장 ROI 에 함께 들어와
    # 연결성분이 늘고 간격 적합이 깨진다.
    tick_h = 8.0
    for i in (0, marks - 1):
        shapes.append(
            Rect(x0 + i * pitch_mm - TICK_W / 2,
                 cy - mark_d_mm / 2 - QUIET_MM - tick_h, TICK_W, tick_h)
        )

    vr_len = min(VERIFY_LINE_MM, pw - 40.0)
    vs, vt = _verify_ruler((pw - vr_len) / 2, 26.0, vr_len)
    shapes += vs

    below = cy - mark_d_mm / 2 - QUIET_MM - tick_h
    texts = vt + [
        Text(x0, cy + mark_d_mm / 2 + QUIET_MM,
             f"AIVIS length gauge - {marks} marks @ {pitch_mm:g} mm "
             f"(nominal span {span:.2f} mm)", 10.0),
        Text(x0, below - 8.0,
             "MEASURE the actual tick-to-tick span with a caliper and write "
             "it here: ______________ mm", 9.0),
        Text(x0, below - 16.0,
             f"Then use  pitch = measured_span / {marks - 1}  in AIVIS_FIDUCIAL "
             "- do NOT assume the printed value.", 8.0),
        Text(x0, below - 24.0,
             "Printed paper stretches with humidity. Glue to a flat rigid "
             "plate; prefer a steel/glass scale for production.", 7.5),
        Text(x0, below - 32.0,
             f"Keep the camera ROI within +/-{QUIET_MM:g} mm of the mark row; "
             "text or ticks inside it break the detector.", 7.5),
        Text(x0, 14.0,
             f"AIVIS_FIDUCIAL=<pitch>:<x>,<y>,<w>,<h>   nominal pitch "
             f"{pitch_mm:g}", 8.0),
    ]
    return Sheet(pw, ph, shapes, texts,
                 f"AIVIS gauge {marks}x{pitch_mm:g}mm")


# ------------------------------------------------------------------ CLI

def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="make_targets", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("checkerboard", help="렌즈 왜곡 보정용 체커보드")
    b.add_argument("--cols", type=int, default=9, help="가로 내부 코너 수")
    b.add_argument("--rows", type=int, default=6, help="세로 내부 코너 수")
    b.add_argument("--square-mm", type=float, default=20.0)

    g = sub.add_parser("gauge", help="길이 측정용 기준자")
    g.add_argument("--pitch-mm", type=float, default=10.0)
    g.add_argument("--marks", type=int, default=0, help="0=용지에 가득")
    g.add_argument("--mark-d-mm", type=float, default=0.0, help="0=간격의 50%%")

    for p in (b, g):
        p.add_argument("--page", choices=sorted(PAGES), default="a4")
        p.add_argument("--portrait", action="store_true", help="세로(기본 가로)")
        p.add_argument("--out", type=Path, required=True)

    a = ap.parse_args(argv)
    try:
        if a.cmd == "checkerboard":
            sheet = checkerboard_sheet(
                cols=a.cols, rows=a.rows, square_mm=a.square_mm,
                page=a.page, landscape=not a.portrait)
        else:
            sheet = gauge_sheet(
                pitch_mm=a.pitch_mm, marks=a.marks, mark_d_mm=a.mark_d_mm,
                page=a.page, landscape=not a.portrait)
    except ValueError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 2

    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_bytes(to_pdf(sheet))
    print(f"{a.out}  ({sheet.page_w_mm:g}x{sheet.page_h_mm:g}mm, "
          f"도형 {len(sheet.shapes)}개)")
    print("인쇄: 배율 100%(실제 크기), 용지맞춤 끄기. 인쇄 후 기준선을 자로 확인.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
