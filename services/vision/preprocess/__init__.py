"""영상 전처리 모듈 (M2) — CLAUDE.md §4②, §6.2/§6.3 전처리.

제품 영역 자동 분리(ROI), 길이/표면 판정 영역 구분, 금속 반사 보정,
노이즈 제거·정규화. 동일 입력 반복 시 ROI 좌표 편차 ≤ 2px(결정적).

본 모듈은 OpenCV 고전 CV 만 사용하며 무작위성이 없다(결정적).
"""
from __future__ import annotations

from .roi import (
    PreprocessResult,
    Roi,
    preprocess,
    segment_pipe_roi,
)

__all__ = [
    "PreprocessResult",
    "Roi",
    "preprocess",
    "segment_pipe_roi",
]
