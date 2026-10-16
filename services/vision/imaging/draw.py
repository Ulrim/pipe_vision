"""길이 측정 근거 시각화 공용 드로잉 헬퍼 (§6.2 투명화).

사용자 피드백②("끝단과 끝단을 어떻게 정하는지 이해가 안 된다 / 길이가 정확히
안 나온다")에 대응해, 결과 오버레이 이미지에 시스템이 잡은 **양 끝단 위치 +
두 끝단을 잇는 측정 스팬 라인 + 측정값/편차/판정** 을 그려 작업자가 측정 근거를
눈으로 보게 한다. 단일(render_overlay)과 배치(render_batch_overlay) 오버레이가
공통으로 재사용한다. tools/debug_length.py 의 점선 헬퍼도 여기로 모아 중복을
없앤다(단일 진실원).

OpenCV 만 사용하고 한글 텍스트는 쓰지 않는다(HERSHEY 폰트 한글 미지원 → 깨짐).
드로잉은 판정(proc_time_ms) 계측 이후에만 호출되므로 처리속도 KPI(<300ms)에
영향을 주지 않는다.

검사 영역(ROI, 2026-10-16): draw_inspect_area 가 결과 사진에 노란 점선 사각형을
그린다 — 작업자 HMI·실시간 현황은 이 사진을 그대로 보여 주므로 화면에도 나온다.
"""
from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

from ..length.measure import LengthSpan

_FONT = cv2.FONT_HERSHEY_SIMPLEX
_BLACK = (0, 0, 0)
_WHITE = (255, 255, 255)


def dashed_vline(
    canvas: np.ndarray,
    x: float,
    y0: int,
    y1: int,
    color,
    *,
    dash: int = 6,
    gap: int = 5,
    thickness: int = 1,
) -> None:
    """세로 점선(끝단 정수 위치 표시 등). debug_length 와 공용."""
    xi = int(round(x))
    y = int(y0)
    end = int(y1)
    while y < end:
        y2 = min(y + dash, end)
        cv2.line(canvas, (xi, y), (xi, y2), color, thickness, cv2.LINE_AA)
        y = y2 + gap


def dashed_hline(
    canvas: np.ndarray,
    y: float,
    x0: int,
    x1: int,
    color,
    *,
    dash: int = 8,
    gap: int = 6,
    thickness: int = 1,
) -> None:
    """가로 점선(세그멘테이션 seam 경계 등). debug_length 와 공용."""
    yi = int(round(y))
    x = int(x0)
    end = int(x1)
    while x < end:
        x2 = min(x + dash, end)
        cv2.line(canvas, (x, yi), (x2, yi), color, thickness, cv2.LINE_AA)
        x = x2 + gap


def draw_length_span(
    canvas: np.ndarray,
    span: Optional[LengthSpan],
    *,
    color,
    label: Optional[str] = None,
    thickness: int = 2,
    txt_scale: float = 0.5,
) -> None:
    """끝단 세로 마커 2개 + 두 끝단을 잇는 측정 스팬(양방향 화살표) + 라벨을 그린다.

    span 좌표는 원본 프레임 좌표계. left_x/right_x 가 없으면(끝단 미검출) 아무것도
    그리지 않는다(측정 불가 상태를 거짓 표기하지 않음). 모든 좌표는 프레임 안으로
    클리핑한다(배치 crop 오차/경계 안전).
    """
    if span is None or not span.valid:
        return
    h, w = canvas.shape[:2]

    def _cx(v: float) -> int:
        return int(min(max(0, round(v)), w - 1))

    def _cy(v: int) -> int:
        return int(min(max(0, v), h - 1))

    lx = _cx(span.left_x)
    rx = _cx(span.right_x)
    y_top = _cy(span.y_top)
    y_bot = _cy(span.y_bottom)
    if y_bot <= y_top:
        y_bot = _cy(y_top + 1)
    y_mid = (y_top + y_bot) // 2

    # 좌/우 끝단 세로 마커(측정에 쓰인 실제 끝단 x).
    cv2.line(canvas, (lx, y_top), (lx, y_bot), color, thickness, cv2.LINE_AA)
    cv2.line(canvas, (rx, y_top), (rx, y_bot), color, thickness, cv2.LINE_AA)

    # 두 끝단을 잇는 측정 스팬(양방향 화살표) — "이 거리를 쟀다"를 명시.
    if rx != lx:
        cv2.arrowedLine(
            canvas, (lx, y_mid), (rx, y_mid), color, thickness, cv2.LINE_AA,
            tipLength=0.02,
        )
        cv2.arrowedLine(
            canvas, (rx, y_mid), (lx, y_mid), color, thickness, cv2.LINE_AA,
            tipLength=0.02,
        )

    if not label:
        return
    # 라벨은 스팬 중앙 위에 검은 배경 박스로(가독성). 프레임을 벗어나지 않게 배치.
    (tw, th), _ = cv2.getTextSize(label, _FONT, txt_scale, 1)
    cxm = (lx + rx) // 2
    tx = int(min(max(2, cxm - tw // 2), max(2, w - tw - 2)))
    ty = y_top - 6
    if ty - th - 4 < 0:
        ty = min(h - 2, y_mid + th + 8)
    cv2.rectangle(
        canvas, (tx - 2, ty - th - 4), (tx + tw + 2, ty + 3), _BLACK, thickness=-1
    )
    cv2.putText(
        canvas, label, (tx, ty), _FONT, txt_scale, _WHITE, 1, cv2.LINE_AA
    )


#: 검사 영역 색(BGR 노랑) — OK 초록·NG 빨강·개수 흰색과 겹치지 않는다.
ROI_COLOR = (0, 215, 255)
#: 영역 밖을 어둡게 하는 정도(0=그대로, 1=검정). '여기는 안 봤다' 가 한눈에 보이게.
ROI_DIM = 0.45


def draw_inspect_area(
    canvas: np.ndarray,
    rect,
    *,
    top_reserved: int = 0,
    txt_scale: Optional[float] = None,
) -> None:
    """검사 영역(ROI)을 그린다 — 영역 밖은 어둡게, 경계는 노란 점선, 'ROI' 라벨.

    rect: preprocess.Roi(원본 프레임 픽셀) 또는 None(화면 전체 → 아무것도 안 그림).
    오버레이를 그리기 **전에** 먼저 부른다 — 판정 표시(튜브 박스·헤더)가 그 위에 온다.
    top_reserved: 위쪽 헤더 높이. 라벨이 헤더에 가리지 않게 그 아래로 내린다.
    """
    if rect is None:
        return
    h, w = canvas.shape[:2]
    x0 = int(min(max(0, rect.x0), w - 1))
    y0 = int(min(max(0, rect.y0), h - 1))
    x1 = int(min(max(x0 + 1, rect.x1 - 1), w - 1))
    y1 = int(min(max(y0 + 1, rect.y1 - 1), h - 1))

    # 1) 영역 밖 어둡게(영역 안 픽셀은 그대로 — 판독을 방해하지 않는다).
    inside = canvas[y0:y1 + 1, x0:x1 + 1].copy()
    canvas[:] = (canvas.astype(np.float32) * (1.0 - ROI_DIM)).astype(canvas.dtype)
    canvas[y0:y1 + 1, x0:x1 + 1] = inside

    # 2) 경계 점선(해상도에 비례한 두께·간격).
    base = min(h, w)
    thick = max(2, int(round(base / 400)))
    dash = max(10, base // 40)
    gap = max(6, dash // 2)
    for y in (y0, y1):
        dashed_hline(canvas, y, x0, x1, ROI_COLOR, dash=dash, gap=gap, thickness=thick)
    for x in (x0, x1):
        dashed_vline(canvas, x, y0, y1, ROI_COLOR, dash=dash, gap=gap, thickness=thick)

    # 3) 라벨 — 영역 위 바깥에 자리가 있으면 거기(판정 표시와 안 겹친다), 없으면 안쪽.
    scale = txt_scale if txt_scale is not None else max(0.6, base / 900.0)
    label = "ROI"
    (tw, th), _ = cv2.getTextSize(label, _FONT, scale, 2)
    pad = 4
    if y0 - th - 2 * pad >= top_reserved:
        ty = y0 - pad - thick
    else:
        ty = max(y0, top_reserved) + th + pad + thick
    tx = int(min(max(2, x0 + thick + pad), max(2, w - tw - 2 * pad)))
    ty = int(min(max(th + pad, ty), h - pad))
    cv2.rectangle(canvas, (tx - pad, ty - th - pad), (tx + tw + pad, ty + pad), _BLACK, -1)
    cv2.putText(canvas, label, (tx, ty), _FONT, scale, ROI_COLOR, 2, cv2.LINE_AA)


__all__ = [
    "ROI_COLOR",
    "dashed_vline",
    "dashed_hline",
    "draw_inspect_area",
    "draw_length_span",
]
