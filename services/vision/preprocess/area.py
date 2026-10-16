"""검사 영역(ROI) — 설정 하나로 촬영·검사·화면 표시를 맞춘다 (2026-10-16).

카메라 화면에는 제품 말고도 작업자 통로, 옆 크레이트, 공구가 들어온다. 검사 영역은
화면 비율 "x0,y0,x1,y1"(0~1 — 해상도와 무관)로 정하고 세 곳이 같은 값을 쓴다.

1. **영상 트리거** — 이 영역의 움직임만 본다(acquisition/trigger.py).
2. **검사** — 이 영역 안의 제품만 나누고·세고·잰다. 영역 밖 제품은 없는 것으로 친다
   (다발: 영역 안에서 튜브를 나눈다 / 크레이트: 영역 안 단면만 센다 / 단일: 영역 안에서
   가장 큰 제품). 돌려주는 좌표는 전부 **원본 프레임 기준**이라 라벨링 자르기
   (tube_bbox)와 결과 오버레이가 그대로 맞는다.
3. **결과 사진** — 노란 점선 사각형과 'ROI' 로 그린다(imaging/draw.draw_inspect_area).
   작업자 HMI·실시간 현황이 그 사진을 그대로 보여 준다. 제품 끝이 사각형 밖으로 나가
   있으면 그 끝은 검사되지 않는다는 뜻이다 — 길이 스테이션은 양 끝단까지 들어오게 잡는다.

환경변수 `AIVIS_ROI`(같은 날 먼저 쓴 이름 `AIVIS_VISION_ROI` 도 읽는다). 비우거나
잘못 적으면 화면 전체 — 종전 동작과 같고 사진에 사각형도 그리지 않는다.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

from .roi import Roi

log = logging.getLogger("aivis.vision.area")

ENV_ROI = "AIVIS_ROI"
LEGACY_ENV_ROI = "AIVIS_VISION_ROI"
FULL = (0.0, 0.0, 1.0, 1.0)
_MIN_SPAN = 0.02  # 화면의 2% 보다 좁은 영역은 오타로 본다


def parse_area(raw: Optional[str]) -> Tuple[float, float, float, float]:
    """"x0,y0,x1,y1"(화면 비율 0~1) → 정렬·클리핑한 튜플. 잘못되면 화면 전체."""
    try:
        x0, y0, x1, y1 = (float(v) for v in str(raw).split(","))
    except (TypeError, ValueError):
        return FULL
    x0, x1 = sorted((min(max(x0, 0.0), 1.0), min(max(x1, 0.0), 1.0)))
    y0, y1 = sorted((min(max(y0, 0.0), 1.0), min(max(y1, 0.0), 1.0)))
    if x1 - x0 < _MIN_SPAN or y1 - y0 < _MIN_SPAN:
        return FULL
    return (x0, y0, x1, y1)


def _says_full(raw: str) -> bool:
    """일부러 화면 전체("0,0,1,1")를 적었나(형식 오류와 구분해 경고하지 않으려고)."""
    try:
        return tuple(float(v) for v in raw.split(",")) == FULL
    except ValueError:
        return False


@dataclass(frozen=True)
class InspectArea:
    """검사 영역(화면 비율). 기본값 = 화면 전체."""

    x0: float = 0.0
    y0: float = 0.0
    x1: float = 1.0
    y1: float = 1.0

    @staticmethod
    def parse(raw: Optional[str]) -> "InspectArea":
        return InspectArea(*parse_area(raw))

    @staticmethod
    def from_env() -> "InspectArea":
        raw = (os.environ.get(ENV_ROI) or os.environ.get(LEGACY_ENV_ROI) or "").strip()
        area = InspectArea.parse(raw)
        if raw and area.full and not _says_full(raw):
            log.warning("%s=%r 형식 오류 — 화면 전체를 검사한다(예: 0.05,0.20,0.95,0.80)",
                        ENV_ROI, raw)
        return area

    @property
    def fractions(self) -> Tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)

    @property
    def full(self) -> bool:
        return self.fractions == FULL

    def pixels(self, width: int, height: int) -> Roi:
        """이 해상도에서의 픽셀 사각형(최소 2px, 화면 안)."""
        xa = min(max(int(round(self.x0 * width)), 0), max(0, width - 2))
        ya = min(max(int(round(self.y0 * height)), 0), max(0, height - 2))
        xb = min(width, max(xa + 2, int(round(self.x1 * width))))
        yb = min(height, max(ya + 2, int(round(self.y1 * height))))
        return Roi(xa, ya, xb, yb)

    def text(self) -> str:
        return ",".join(f"{v:g}" for v in self.fractions)


def area_rect(area: Optional[InspectArea], frame: np.ndarray) -> Optional[Roi]:
    """프레임 위의 검사 영역 픽셀 사각형. 영역이 없거나 화면 전체면 None(자를 것 없음)."""
    if area is None or area.full or frame is None or getattr(frame, "ndim", 0) < 2:
        return None
    h, w = frame.shape[:2]
    return area.pixels(w, h)


__all__ = ["ENV_ROI", "LEGACY_ENV_ROI", "InspectArea", "area_rect", "parse_area"]
