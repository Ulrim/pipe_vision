"""길이 측정 모듈 (M3) — CLAUDE.md §6.2 고전 CV(서브픽셀 엣지).

ROI 그레이 → 이진화/Canny → 양 끝단 에지 → 서브픽셀 보간 → 끝단좌표 →
px 거리 × px_to_mm_scale(item_master) → deviation → 공차 OK/NG.

끝단 검출 실패 시 edge_detected=False, meas=None, length_verdict=NG.
LengthResult(aivis_types) 를 반환하며 proc_time_ms 를 계측한다. 예산 ≤80ms.
"""
from __future__ import annotations

from .measure import EdgeEndpoints, LengthSpan, measure_length, measure_length_ex

__all__ = [
    "measure_length",
    "measure_length_ex",
    "EdgeEndpoints",
    "LengthSpan",
]
