"""단면 번들 카운터 단위 테스트 (multi/bundle.py).

공개 데이터셋 성능은 tools/bundle_benchmark.py 로 따로 잰다. 여기서는
합성 영상으로 **결정성**과 **기본 동작**만 고정한다.
"""
from __future__ import annotations

import cv2
import numpy as np

from vision.multi.bundle import count_bundle


def _hex_bundle(rows: int, cols: int, r: int = 12, pitch: int = 28) -> np.ndarray:
    """밝은 금속 벽 위에 어두운 보어(관 안쪽)가 육각 충전된 합성 번들."""
    h = pitch * rows + 2 * pitch
    w = pitch * cols + 2 * pitch
    img = np.full((h, w, 3), 200, np.uint8)
    for i in range(rows):
        for j in range(cols):
            cx = pitch + j * pitch + (pitch // 2 if i % 2 else 0)
            cy = pitch + int(i * pitch * 0.87)
            cv2.circle(img, (cx, cy), r, (40, 40, 40), -1)
    return img


def test_detects_most_bores() -> None:
    img = _hex_bundle(6, 6)
    res = count_bundle(img)
    # 경계에 잘린 것이 있어 정확히 36 을 요구하지 않는다. 절반 이상은 찾아야 한다.
    assert res.count >= 18
    assert res.median_radius > 0


def test_deterministic() -> None:
    img = _hex_bundle(5, 5)
    a = count_bundle(img)
    b = count_bundle(img)
    assert a.count == b.count
    assert [(d.cx, d.cy) for d in a.detections] == [(d.cx, d.cy) for d in b.detections]


def test_empty_frame_is_safe() -> None:
    blank = np.full((64, 64, 3), 128, np.uint8)
    res = count_bundle(blank)
    assert res.count == 0
    assert res.detections == []


def test_handles_degenerate_input() -> None:
    assert count_bundle(np.zeros((0, 0, 3), np.uint8)).count == 0
