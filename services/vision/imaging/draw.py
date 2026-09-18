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


__all__ = ["dashed_vline", "dashed_hline", "draw_length_span"]
