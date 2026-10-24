"""튜브 표면 불량 그리기 — 부록 A.5 정의를 튜브 좌표(축 u, 둘레 v)에서.

| 코드 | 그리는 것 | 경계(BORDER) |
|---|---|---|
| SCR | 표면 선형 흠집: 축에 가까운 방향(±35°)의 가는 선 1~3개, 어둡거나(파임) 밝게(긁혀 반짝임) | 대비 낮고 짧게 |
| OIL | 세척 후 유분 잔존: 불규칙한 얼룩 — 반사 띠가 죽고(무광) 약간 어둡고, 엷은 무지개 기 | 엷게 |
| DIS | 변색: 은 → 금 → 주황 → 갈색 단계, 띠 모양 구간이나 얼룩 | 아주 옅은 금빛 |

복합(MULTI)은 두 종 이상을 함께 그리고 라벨에 각 코드 + MULTI 를 남긴다(§7.2 배열 규칙).
그리는 영역은 튜브 실루엣 안, 끝 근처(표면 판정에서 빼는 6%)는 피한다.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

import cv2
import numpy as np

from .tube import TubePatch

DEFECT_CODES = ("SCR", "OIL", "DIS")
#: 변색 단계 — B, G, R 배율(은=무변화). 부록 A.5 의 은→금→주황→갈색.
DIS_STAGES = {
    "gold": (0.78, 0.94, 1.04),
    "orange": (0.62, 0.82, 1.08),
    "brown": (0.55, 0.62, 0.74),
}


def _inside(p: TubePatch, margin_u: float = 0.08) -> np.ndarray:
    return (p.alpha > 0.5) & (p.un > margin_u) & (p.un < 1 - margin_u)


def _blob_mask(shape, rng, *, cx, cy, rx, ry, rough: float = 0.45) -> np.ndarray:
    """불규칙한 얼룩(0~1, 가장자리 부드럽게)."""
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = ((xx - cx) / max(rx, 1.0)) ** 2 + ((yy - cy) / max(ry, 1.0)) ** 2
    lo = rng.random((max(2, h // 6 + 2), max(2, w // 12 + 2))).astype(np.float32)
    noise = cv2.resize(lo, (w, h), interpolation=cv2.INTER_CUBIC)
    m = np.clip(1.0 - d - rough * (noise - 0.5), 0, 1)
    return cv2.GaussianBlur(m, (0, 0), max(1.0, 0.15 * min(rx, ry)))


def scratch(p: TubePatch, rng: np.random.Generator, *, strength: float = 1.0) -> None:
    rows, cols = p.alpha.shape
    delta = np.zeros((rows, cols), np.float32)
    for _ in range(int(rng.integers(1, 4))):
        L = p.length * rng.uniform(0.04, 0.22) * (0.5 + 0.5 * strength)
        ang = np.radians(rng.uniform(-35, 35))
        u0 = p.pad + rng.uniform(0.1, 0.9 - L / max(p.length, 1)) * p.length
        v0 = rows / 2 + rng.uniform(-0.6, 0.6) * p.diameter / 2
        n = 8
        t = np.linspace(0, 1, n)
        bend = rng.uniform(-0.15, 0.15) * p.diameter * np.sin(np.pi * t)
        xs = u0 + t * L * np.cos(ang)
        ys = v0 + t * L * np.sin(ang) * 0.35 + bend
        pts = np.column_stack([xs, ys]).round().astype(np.int32)
        width = int(rng.choice([1, 1, 2, 2, 3]))
        sign = -1.0 if rng.random() < 0.6 else 1.0
        amp = sign * rng.uniform(30, 95) * strength
        line = np.zeros_like(delta)
        cv2.polylines(line, [pts], False, 1.0, thickness=width, lineType=cv2.LINE_AA)
        delta += amp * cv2.GaussianBlur(line, (0, 0), 0.6)
    delta *= _inside(p)
    p.rgb += delta[..., None]


def oil(p: TubePatch, rng: np.random.Generator, *, strength: float = 1.0) -> None:
    rows, cols = p.alpha.shape
    for _ in range(int(rng.integers(1, 3))):
        rx = p.length * rng.uniform(0.04, 0.16)
        ry = p.diameter * rng.uniform(0.25, 0.6)
        cx = p.pad + rng.uniform(0.12, 0.88) * p.length
        cy = rows / 2 + rng.uniform(-0.3, 0.3) * p.diameter / 2
        m = _blob_mask((rows, cols), rng, cx=cx, cy=cy, rx=rx, ry=ry) * _inside(p)
        m = (m * (0.55 + 0.45 * strength))[..., None]
        flat = cv2.GaussianBlur(p.rgb, (0, 0), max(2.0, 0.25 * p.diameter))
        target = 0.5 * p.rgb + 0.5 * flat - 14.0 * strength
        sheen = 7.0 * strength * np.sin(np.linspace(0, rng.uniform(3, 9), cols))[None, :]
        target = target + np.dstack([sheen, -0.5 * sheen, -sheen])
        p.rgb = p.rgb * (1 - m) + target * m


def discolor(p: TubePatch, rng: np.random.Generator, *, strength: float = 1.0,
             stage: Optional[str] = None) -> str:
    rows, cols = p.alpha.shape
    stage = stage or str(rng.choice(list(DIS_STAGES)))
    mul = np.array(DIS_STAGES[stage], np.float32)
    if rng.random() < 0.5:   # 축 방향 띠(열 변색)
        u0 = rng.uniform(0.1, 0.6)
        u1 = min(0.92, u0 + rng.uniform(0.1, 0.4))
        m = np.clip(np.minimum(p.un - u0, u1 - p.un) * p.length / max(4.0, 0.3 * p.diameter), 0, 1)
    else:                    # 얼룩
        m = _blob_mask((rows, cols), rng, cx=p.pad + rng.uniform(0.15, 0.85) * p.length,
                       cy=rows / 2, rx=p.length * rng.uniform(0.06, 0.2), ry=p.diameter * 0.7)
    m = (m * _inside(p) * (0.35 + 0.65 * strength))[..., None]
    p.rgb = p.rgb * (1 - m) + (p.rgb * mul[None, None, :]) * m
    return stage


_PAINT = {"SCR": scratch, "OIL": oil, "DIS": discolor}


def paint_defects(p: TubePatch, codes: Sequence[str], rng: np.random.Generator, *,
                  border: bool = False) -> List[str]:
    """codes 를 그려 넣고 라벨 배열을 돌려준다(2종 이상이면 MULTI 추가). border=True 면 옅게."""
    strength = float(rng.uniform(0.18, 0.32)) if border else float(rng.uniform(0.6, 1.0))
    labels: List[str] = []
    for c in codes:
        if c not in _PAINT:
            raise ValueError(f"모르는 불량 코드: {c}")
        _PAINT[c](p, rng, strength=strength)
        labels.append(c)
    p.rgb = np.clip(p.rgb, 0, 255).astype(np.float32)
    if len(labels) >= 2:
        labels.append("MULTI")
    p.labels = labels
    p.border = border
    return labels


__all__ = ["DEFECT_CODES", "DIS_STAGES", "discolor", "oil", "paint_defects", "scratch"]
