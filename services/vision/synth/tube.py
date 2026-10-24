"""튜브 한 개의 겉모습 — 축 정렬 패치(가로 = 축 u, 세로 = 둘레 v).

알루미늄 원통을 위에서 볼 때의 특징만 담는다.
- 둘레 방향 음영: 가장자리로 갈수록 어둡고(스치는 각), 조명 쪽에 좁은 반사 띠(정반사)와 넓은 2차 반사.
- 축 방향: 반사 띠 세기가 천천히 바뀌고(주변 반사), 압출 결(가는 세로줄)과 잔무늬가 있다.
- 끝: 절단면 쪽이 어둡게 보이거나(그늘) 비스듬히 보면 구멍(내경)이 보인다.

패치는 실루엣(알파, 부분 화소 포함)과 함께 나온다 — 장면에 회전·배치할 때 그대로 합성한다.
좌표 격자(un: 0~1 축 위치, vn: -1~1 둘레 위치)도 같이 줘서 불량을 튜브 좌표에서 그릴 수 있다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np


@dataclass
class TubeLook:
    """겉모습 매개변수(재현용으로 정답에 남긴다)."""

    base: float = 120.0          # 바탕 밝기
    spec_amp: float = 95.0       # 정반사 띠 세기
    spec_center: float = -0.25   # 띠 위치(vn)
    spec_width: float = 0.16     # 띠 폭(vn)
    glow_amp: float = 25.0       # 넓은 2차 반사
    glow_center: float = 0.35
    edge_dark: float = 55.0      # 가장자리 어두워짐
    streaks: float = 6.0         # 압출 결 세기
    along_var: float = 0.25      # 축 방향 반사 세기 변화
    noise: float = 2.5
    tint: tuple = (1.0, 1.0, 1.0)  # B, G, R 배율(알루미늄은 거의 무채색)
    bore_end: int = 0            # 0 없음 / 1 왼쪽 / 2 오른쪽 / 3 양쪽 끝 구멍
    saturate: bool = False       # 반사 포화(하얗게 날아감)

    def jitter(self, rng: np.random.Generator) -> "TubeLook":
        """같은 조명 아래 이웃 튜브 — 반사 띠 위치·세기만 조금씩 다르다(실제 다발이 그렇다)."""
        from dataclasses import replace
        return replace(
            self,
            base=float(np.clip(self.base + rng.normal(0, 6), 60, 200)),
            spec_amp=float(max(10.0, self.spec_amp * rng.uniform(0.85, 1.15))),
            spec_center=float(np.clip(self.spec_center + rng.normal(0, 0.05), -0.8, 0.6)),
            spec_width=float(np.clip(self.spec_width * rng.uniform(0.85, 1.15), 0.04, 0.35)),
            glow_amp=float(max(0.0, self.glow_amp * rng.uniform(0.8, 1.2))),
            streaks=float(max(0.5, self.streaks * rng.uniform(0.7, 1.3))),
            along_var=float(np.clip(self.along_var * rng.uniform(0.7, 1.3), 0.0, 0.6)),
            bore_end=int(rng.choice([0, 0, 1, 2, 3])),
            saturate=bool(self.saturate and rng.random() < 0.7),
        )

    @staticmethod
    def random(rng: np.random.Generator, *, light_bg: bool = False) -> "TubeLook":
        return TubeLook(
            base=float(rng.uniform(95, 150)),
            spec_amp=float(rng.uniform(55, 125)),
            spec_center=float(rng.uniform(-0.55, 0.25)),
            spec_width=float(rng.uniform(0.07, 0.24)),
            glow_amp=float(rng.uniform(0, 45)),
            glow_center=float(rng.uniform(-0.2, 0.6)),
            edge_dark=float(rng.uniform(25, 75)),
            streaks=float(rng.uniform(2, 11)),
            along_var=float(rng.uniform(0.05, 0.45)),
            noise=float(rng.uniform(1.0, 4.0)),
            tint=(float(rng.uniform(0.97, 1.05)), 1.0, float(rng.uniform(0.96, 1.03))),
            bore_end=int(rng.choice([0, 0, 1, 2, 3])),
            saturate=bool(rng.random() < 0.2),
        )


@dataclass
class TubePatch:
    """렌더된 튜브 — rgb(float32, 0~255, BGR), alpha(0~1), 좌표 격자."""

    rgb: np.ndarray
    alpha: np.ndarray
    un: np.ndarray               # 축 위치 0~1 (끝 바깥은 0 미만·1 초과)
    vn: np.ndarray               # 둘레 위치 -1~1
    length: float                # 실제 길이 px(부분 화소)
    diameter: float
    pad: int                     # 끝 바깥 여백 px
    look: Optional[TubeLook] = None
    labels: list = field(default_factory=list)   # 그려 넣은 불량 코드
    border: bool = False


def _lowfreq(n: int, k: int, rng: np.random.Generator) -> np.ndarray:
    """길이 n 의 부드러운 무작위 곡선(-1~1)."""
    pts = rng.uniform(-1, 1, max(2, k))
    return np.interp(np.linspace(0, len(pts) - 1, n), np.arange(len(pts)), pts)


def render_tube(length: float, diameter: float, rng: np.random.Generator, *,
                look: Optional[TubeLook] = None, pad: int = 3, light_bg: bool = False) -> TubePatch:
    """축 정렬 튜브 패치. 높이 = ceil(지름)+2, 너비 = ceil(길이)+2·pad."""
    look = look or TubeLook.random(rng, light_bg=light_bg)
    rows = int(np.ceil(diameter)) + 2
    cols = int(np.ceil(length)) + 2 * pad
    y = np.arange(rows, dtype=np.float32)[:, None] - (rows - 1) / 2.0
    x = np.arange(cols, dtype=np.float32)[None, :] - pad
    vn = np.broadcast_to(y / (diameter / 2.0), (rows, cols)).astype(np.float32)
    un = np.broadcast_to(x / max(length, 1.0), (rows, cols)).astype(np.float32)
    cov_v = np.clip(diameter / 2.0 - np.abs(y) + 0.5, 0, 1)
    cov_u = np.clip(np.minimum(x + 0.5, length - x + 0.5), 0, 1)
    alpha = (cov_v * cov_u).astype(np.float32)

    a = np.abs(vn)
    along = 1.0 + look.along_var * _lowfreq(cols, 6, rng)[None, :]
    spec = look.spec_amp * np.exp(-((vn - look.spec_center) / look.spec_width) ** 2) * along
    glow = look.glow_amp * np.exp(-((vn - look.glow_center) / 0.35) ** 2)
    edge = look.edge_dark * np.clip((a - 0.55) / 0.45, 0, 1) ** 1.5
    inten = look.base + spec + glow - edge
    inten = inten + 6.0 * _lowfreq(cols, 4, rng)[None, :]
    # 압출 결: 둘레 위치마다 축을 따라 이어지는 가는 줄
    streak = rng.normal(0, 1, rows).astype(np.float32)
    streak = cv2.GaussianBlur(streak.reshape(-1, 1), (1, 3), 0).ravel()
    inten = inten + look.streaks * streak[:, None] * (0.6 + 0.4 * np.abs(_lowfreq(cols, 9, rng)))[None, :]
    if look.saturate:
        inten = inten + 40.0 * np.exp(-((vn - look.spec_center) / (look.spec_width * 0.6)) ** 2)
    inten = inten + rng.normal(0, look.noise, (rows, cols))
    # 끝: 절단면 쪽 그늘 + 구멍(비스듬히 보일 때)
    for end, on in ((0, look.bore_end in (1, 3)), (1, look.bore_end in (2, 3))):
        du = (un if end == 0 else 1.0 - un) * length       # 끝에서 안쪽 px
        shade = np.clip(1.0 - du / max(2.0, 0.08 * diameter), 0, 1)
        inten = inten - 35.0 * shade
        if on:
            r = 0.38 * diameter
            bore = ((du / (0.35 * r)) ** 2 + (vn * diameter / 2 / r) ** 2) < 1.0
            inten = np.where(bore & (du >= 0), 30.0 + 10.0 * rng.random(), inten)
    inten = np.clip(inten, 0, 255).astype(np.float32)
    rgb = np.dstack([inten * look.tint[0], inten * look.tint[1], inten * look.tint[2]])
    return TubePatch(rgb=np.clip(rgb, 0, 255).astype(np.float32), alpha=alpha, un=un, vn=vn,
                     length=float(length), diameter=float(diameter), pad=pad, look=look)


def body_crop(patch: TubePatch, *, inset: float = 0.06, frac_v: float = 1.0) -> np.ndarray:
    """표면 판정 영역과 같은 자르기 — 끝에서 inset 만큼 안쪽, 둘레는 frac_v(가운데 기준)."""
    rows, cols = patch.alpha.shape
    u0 = patch.pad + int(round(inset * patch.length))
    u1 = patch.pad + int(round((1 - inset) * patch.length))
    hv = 0.5 * frac_v * patch.diameter
    c = (rows - 1) / 2.0
    v0, v1 = int(np.floor(c - hv)), int(np.ceil(c + hv)) + 1
    return np.clip(patch.rgb[max(0, v0):min(rows, v1), u0:u1], 0, 255).astype(np.uint8)


__all__ = ["TubeLook", "TubePatch", "body_crop", "render_tube"]
