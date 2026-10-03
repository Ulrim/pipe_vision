"""인쇄 타깃의 **치수가 맞는지** 검증.

"PDF 가 생성된다"는 의미가 없다. 검증해야 할 것은 두 가지다.

1. **mm → pt 환산이 정확한가.** 여기가 틀리면 인쇄물이 통째로 틀린 크기로
   나오고, 그 오차는 길이 측정에 그대로 들어간다.
2. **그 기하를 실제로 검출할 수 있는가.** 치수가 맞아도 OpenCV 가 체커보드를
   못 찾거나 마크가 서로 붙으면 쓸모가 없다.

2번은 PDF 를 래스터화해야 하는데 이 환경에 poppler/ghostscript 가 없다.
대신 **PDF 와 똑같은 Sheet 기하**를 numpy 로 그려 검출기에 넣는다. PDF 작성기와
래스터라이저가 같은 입력을 쓰므로, 기하의 오류는 여기서 잡힌다(PDF 바이트
자체는 1번과 구조 테스트로 본다).
"""
from __future__ import annotations

import math
import re

import cv2
import numpy as np
import pytest

from vision.calib.fiducial import measure_scale
from vision.tools.make_targets import (
    MM_PER_INCH,
    PAGES,
    Circle,
    Rect,
    Sheet,
    Text,
    VERIFY_TICK_H,
    checkerboard_sheet,
    gauge_sheet,
    mm2pt,
    to_pdf,
)


def rasterize(sheet: Sheet, dpi: float = 200.0) -> np.ndarray:
    """Sheet → 8bit 그레이. 흰 바탕에 검은 도형(인쇄물과 같은 극성).

    PDF 좌표는 왼쪽 **아래**가 원점이고 이미지는 왼쪽 **위**가 원점이라
    y 를 뒤집는다 — 여기를 틀리면 상하 반전된 것을 검증하게 된다.
    """
    px_per_mm = dpi / MM_PER_INCH
    w = int(round(sheet.page_w_mm * px_per_mm))
    h = int(round(sheet.page_h_mm * px_per_mm))
    img = np.full((h, w), 255, np.uint8)

    def X(mm: float) -> float:
        return mm * px_per_mm

    def Y(mm: float) -> float:
        return h - mm * px_per_mm

    for sh in sheet.shapes:
        if isinstance(sh, Rect):
            cv2.rectangle(
                img,
                (int(round(X(sh.x))), int(round(Y(sh.y + sh.h)))),
                (int(round(X(sh.x + sh.w))), int(round(Y(sh.y)))),
                0, -1,
            )
        else:
            cv2.circle(img, (int(round(X(sh.cx))), int(round(Y(sh.cy)))),
                       int(round(sh.r * px_per_mm)), 0, -1)
    return img


# ------------------------------------------------- 1) 단위 환산 / PDF 구조

def test_mm_to_pt_is_the_pdf_definition():
    """1pt = 1/72 inch. 이 상수가 틀리면 인쇄물 전체가 틀린다."""
    assert mm2pt(25.4) == pytest.approx(72.0)
    assert mm2pt(210.0) == pytest.approx(595.2755905511812)
    assert mm2pt(297.0) == pytest.approx(841.8897637795275)


def test_page_sizes_are_iso_216():
    assert PAGES["a4"] == (210.0, 297.0)
    assert PAGES["a3"] == (297.0, 420.0)


def test_mediabox_matches_the_page_in_points():
    pdf = to_pdf(checkerboard_sheet(page="a4", landscape=True)).decode("latin-1")
    m = re.search(r"/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]", pdf)
    assert m, "MediaBox 가 없다"
    assert float(m.group(1)) == pytest.approx(mm2pt(297.0), abs=1e-3)
    assert float(m.group(2)) == pytest.approx(mm2pt(210.0), abs=1e-3)


def test_squares_land_on_exact_point_coordinates():
    """내용 스트림의 사각형이 mm×72/25.4 그대로인지 — 반올림 사고 방지."""
    sheet = checkerboard_sheet(cols=9, rows=6, square_mm=20.0)
    side = mm2pt(20.0)
    pdf = to_pdf(sheet).decode("latin-1")
    res = [tuple(float(v) for v in m)
           for m in re.findall(r"([\d.]+) ([\d.]+) ([\d.]+) ([\d.]+) re f", pdf)]
    squares = [r for r in res if abs(r[2] - side) < 1e-3]
    assert len(squares) == 35, "10x7 체커보드의 검은 칸은 35개"
    # 고유한 x 좌표는 칸 격자 위에 있어야 한다. (행이 엇갈리므로 1칸·2칸
    # 간격이 모두 나온다.) PDF 는 소수 4자리로 쓰므로 그만큼 허용한다.
    xs = sorted({r[0] for r in squares})
    for a, b in zip(xs, xs[1:]):
        gap = b - a
        assert min(abs(gap - side), abs(gap - 2 * side)) < 1e-3, (
            f"칸 격자를 벗어난 간격 {gap:.4f}pt"
        )
    assert len(xs) == 10, "가로 10칸"


def test_pdf_xref_offsets_point_at_their_objects():
    """xref 가 어긋나면 뷰어가 파일을 못 연다 — 인쇄소에서야 알게 된다."""
    raw = to_pdf(gauge_sheet())
    start = int(re.search(rb"startxref\s+(\d+)", raw).group(1))
    assert raw[start:start + 4] == b"xref"
    offsets = [int(m) for m in re.findall(rb"^(\d{10}) 00000 n", raw[start:],
                                          re.M)]
    assert len(offsets) == 6
    for i, off in enumerate(offsets, start=1):
        assert raw[off:off + len(f"{i} 0 obj")] == f"{i} 0 obj".encode()


def test_sheet_text_rejects_non_ascii():
    """기본 14종 폰트에는 한글이 없다. 조용히 빈칸으로 인쇄되면 안 된다."""
    with pytest.raises(ValueError):
        Text(0, 0, "한글")


# ------------------------------------------------- 2) 실제로 검출되는가

def test_opencv_finds_the_checkerboard_and_counts_corners():
    sheet = checkerboard_sheet(cols=9, rows=6, square_mm=20.0)
    img = rasterize(sheet, dpi=150)
    found, corners = cv2.findChessboardCorners(img, (9, 6), None)
    assert found, "생성한 체커보드를 OpenCV 가 못 찾는다"
    assert corners.shape[0] == 9 * 6


def test_checkerboard_spacing_comes_back_at_the_printed_size():
    """래스터 상의 코너 간격이 20mm 에 해당하는 화소 수와 맞는가."""
    dpi = 150.0
    sheet = checkerboard_sheet(cols=9, rows=6, square_mm=20.0)
    img = rasterize(sheet, dpi=dpi)
    found, corners = cv2.findChessboardCorners(img, (9, 6), None)
    assert found
    c = cv2.cornerSubPix(
        img, corners, (7, 7), (-1, -1),
        (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.01),
    ).reshape(6, 9, 2)
    dx = np.diff(c[:, :, 0], axis=1).mean()
    expect = 20.0 * dpi / MM_PER_INCH
    assert dx == pytest.approx(expect, rel=2e-3)


def test_odd_even_rule_is_enforced():
    """짝수×짝수 보드는 180° 회전이 모호해 코너 순서가 뒤집힐 수 있다."""
    with pytest.raises(ValueError, match="홀짝"):
        checkerboard_sheet(cols=8, rows=6)
    checkerboard_sheet(cols=9, rows=6)      # 통과해야 한다


def test_board_too_big_for_the_page_fails_with_a_hint():
    with pytest.raises(ValueError, match="a3"):
        checkerboard_sheet(cols=9, rows=6, square_mm=40.0, page="a4")


def test_gauge_marks_are_read_back_at_the_printed_pitch():
    """**가장 중요한 테스트.** 생성한 기준자를 우리 검출기가 읽어
    설계 간격을 복원하는가. 여기가 깨지면 길이가 통째로 틀어진다."""
    dpi = 200.0
    pitch = 10.0
    sheet = gauge_sheet(pitch_mm=pitch, page="a4")
    img = rasterize(sheet, dpi=dpi)
    n_marks = sum(1 for s in sheet.shapes if isinstance(s, Circle))

    # 마크 띠만 ROI 로 자른다(아래쪽 기준선·눈금을 끌어오지 않도록).
    cy_mm = sheet.page_h_mm - 42.0
    half = 10.0          # 정숙 구역(15mm) 안 — 시트가 약속한 ROI 범위
    y0 = int(round((sheet.page_h_mm - (cy_mm + half)) * dpi / MM_PER_INCH))
    h = int(round(2 * half * dpi / MM_PER_INCH))
    s = measure_scale(img, pitch_mm=pitch, roi=(0, y0, img.shape[1], h))

    assert s.n_marks == n_marks
    expect_mm_per_px = MM_PER_INCH / dpi
    assert s.mm_per_px == pytest.approx(expect_mm_per_px, rel=1e-3)
    assert s.residual_px < 1.0, "등간격이 깨졌다"


def test_gauge_span_equals_pitch_times_gaps():
    sheet = gauge_sheet(pitch_mm=10.0, marks=11)
    cs = [s for s in sheet.shapes if isinstance(s, Circle)]
    assert len(cs) == 11
    span = cs[-1].cx - cs[0].cx
    assert span == pytest.approx(100.0)
    for a, b in zip(cs, cs[1:]):
        assert b.cx - a.cx == pytest.approx(10.0)


def test_gauge_fills_the_page_by_default_and_beats_the_minimum():
    """마크가 많을수록 등간격 적합이 좋아진다(1/√N) — 기본은 '가득'."""
    sheet = gauge_sheet(pitch_mm=10.0, page="a4")
    n = sum(1 for s in sheet.shapes if isinstance(s, Circle))
    assert n >= 11, "운영 최소 권장치(11개)를 기본값이 만족해야 한다"
    assert (n - 1) * 10.0 <= 297.0 - 2 * 18.0 + 1e-9


def test_marks_too_fat_for_the_pitch_are_rejected():
    """이웃끼리 붙으면 연결성분이 합쳐져 개수가 틀린다."""
    with pytest.raises(ValueError, match="붙어"):
        gauge_sheet(pitch_mm=10.0, mark_d_mm=9.0)


def test_too_many_marks_for_the_page_fails_with_a_hint():
    with pytest.raises(ValueError, match="a3"):
        gauge_sheet(pitch_mm=10.0, marks=100, page="a4")


# ------------------------------------------------- 3) 배율 확인 기준선

def test_verify_ruler_is_exactly_its_stated_length():
    """시트가 '200.00mm' 라고 적어놓고 다른 길이를 그리면 최악이다 —
    사용자가 그 틀린 선을 믿고 프린터를 맞추게 된다."""
    for sheet in (checkerboard_sheet(), gauge_sheet()):
        label = next(t for t in sheet.texts if "VERIFY SCALE" in t.s)
        stated = float(re.search(r"= ([\d.]+) mm", label.s).group(1))
        # 바깥 눈금 두 개의 중심 간 거리를 잰다(라벨이 약속한 정의).
        ticks = sorted(
            (s for s in sheet.shapes
             if isinstance(s, Rect) and abs(s.h - VERIFY_TICK_H) < 1e-9
             and abs(s.w - 0.3) < 1e-9),
            key=lambda r: r.x,
        )
        assert len(ticks) >= 2
        drawn = (ticks[-1].x + ticks[-1].w / 2) - (ticks[0].x + ticks[0].w / 2)
        assert drawn == pytest.approx(stated, abs=1e-6), (
            f"{sheet.title}: 라벨 {stated}mm vs 실제 {drawn}mm"
        )


def test_everything_stays_inside_the_page():
    """도형이 용지 밖으로 나가면 프린터가 잘라낸다 — 기준선이 잘리면 치명적."""
    for sheet in (checkerboard_sheet(), gauge_sheet(),
                  checkerboard_sheet(page="a3", square_mm=30.0),
                  gauge_sheet(page="a3", pitch_mm=10.0)):
        for s in sheet.shapes:
            if isinstance(s, Rect):
                lo_x, hi_x, lo_y, hi_y = s.x, s.x + s.w, s.y, s.y + s.h
            else:
                lo_x, hi_x = s.cx - s.r, s.cx + s.r
                lo_y, hi_y = s.cy - s.r, s.cy + s.r
            assert lo_x >= 0 and lo_y >= 0, f"{sheet.title}: 음수 좌표"
            assert hi_x <= sheet.page_w_mm + 1e-9, f"{sheet.title}: 가로 넘침"
            assert hi_y <= sheet.page_h_mm + 1e-9, f"{sheet.title}: 세로 넘침"


# ------------------------------------------------- 4) 커밋된 산출물 동기화

#: docs/targets/ 에 커밋된 인쇄물 ↔ 생성 인자. 인쇄는 PC 에서 하므로 파일을
#: 저장소에 넣어둔다(현장에서 파이에 파이썬 돌릴 필요 없이 바로 인쇄).
COMMITTED = {
    "checkerboard_A4_9x6_20mm.pdf":
        lambda: checkerboard_sheet(cols=9, rows=6, square_mm=20.0, page="a4"),
    "checkerboard_A3_9x6_30mm.pdf":
        lambda: checkerboard_sheet(cols=9, rows=6, square_mm=30.0, page="a3"),
    "gauge_A4_10mm.pdf": lambda: gauge_sheet(pitch_mm=10.0, page="a4"),
    "gauge_A3_10mm.pdf": lambda: gauge_sheet(pitch_mm=10.0, page="a3"),
    "gauge_A4_5mm.pdf": lambda: gauge_sheet(pitch_mm=5.0, page="a4"),
}


def test_pdf_output_is_deterministic():
    """타임스탬프 같은 게 섞이면 아래 동기화 테스트가 매번 깨진다."""
    assert to_pdf(gauge_sheet()) == to_pdf(gauge_sheet())


@pytest.mark.parametrize("name", sorted(COMMITTED))
def test_committed_pdf_matches_the_generator(name):
    """**커밋된 인쇄물이 생성기와 어긋나면 안 된다.**

    어긋난 채로 두면 현장이 옛 치수를 인쇄하고, 그 오차는 길이값에 그대로
    들어간다. 생성기를 고쳤으면 파일도 다시 뽑아 커밋하라:
        python -m vision.tools.make_targets ... --out docs/targets/<name>
    """
    from pathlib import Path

    repo = Path(__file__).resolve().parents[3]
    path = repo / "docs" / "targets" / name
    assert path.exists(), f"커밋된 인쇄물이 없다: {path}"
    assert path.read_bytes() == to_pdf(COMMITTED[name]()), (
        f"{name} 이 생성기와 다르다 — 다시 생성해 커밋하라"
    )
