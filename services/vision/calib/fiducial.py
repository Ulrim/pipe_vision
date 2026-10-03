"""프레임마다 스케일을 다시 잰다 — 배율이 흔들려도 길이는 안 흔들리게.

`item_master.px_to_mm_scale` 은 **저장된 상수**다. 한 번 보정해 두고 계속 쓴다.
문제는 배율이 가만있지 않는다는 것이다.

* 제품 상면 높이가 Δz 만큼 변하면 배율이 변해 `오차 = 길이 × Δz / 작업거리`.
  250mm 제품을 500mm 거리에서 보면 **높이 1mm 에 0.5mm** 가 틀어진다.
* 초점이 움직이면(오토포커스, 온도) 배율이 변한다.
* 카메라 마운트가 열로 늘거나 진동하면 변한다.

이 전부가 "저장된 상수" 가정을 깬다. 해법은 표준적이다 — **알려진 길이의
기준자를 제품과 같은 평면에 함께 찍고, 매 프레임 그 기준자로 스케일을 다시
계산한다**(§A.3 이 이미 촬영 조건으로 요구하고 있다. 코드가 안 쓰고 있었다).

기준자와 제품이 같은 평면에 있으면 높이가 변해도 **둘이 같이** 변하므로 배율
오차가 1차로 상쇄된다. 상수 보정으로는 절대 못 하는 일이다.

## 기준자 만들기

무광 흑색 바탕에 흰 마크(또는 반대) **3개 이상**을 같은 간격으로 둔 막대면
충분하다. 마크가 많을수록 적합이 좋아진다(1/√N).

**중요**: 기준자는 **제품 상면과 같은 높이**에 둬야 한다. 바닥판에 눕히면
튜브 OD 의 절반만큼 평면이 달라 계통오차가 남는다(250mm·WD500·OD12 면 3mm —
공차의 30배다). 품목이 바뀌어 OD 가 달라지면 기준자 높이도 따라가야 한다.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np

try:  # OpenCV 는 vision 서비스의 기본 의존이지만, 임포트 실패해도 죽지 않게.
    import cv2
except Exception:  # pragma: no cover
    cv2 = None  # type: ignore


#: 기준자 설정 환경변수. "pitch_mm:x,y,w,h" 형식.
#:   AIVIS_FIDUCIAL="10:0,0,4056,220"
#: 미설정이면 기준자를 쓰지 않는다(저장된 px_to_mm_scale 로 동작).
ENV_FIDUCIAL = "AIVIS_FIDUCIAL"


@dataclass(frozen=True)
class FiducialConfig:
    """기준자 띠의 위치와 눈금 간격."""

    pitch_mm: float
    roi: Tuple[int, int, int, int]
    dark_marks: bool = True

    @staticmethod
    def parse(spec: str) -> "FiducialConfig":
        """'10:0,0,4056,220' → FiducialConfig. 형식이 틀리면 설명과 함께 실패."""
        try:
            pitch_s, roi_s = spec.split(":", 1)
            x, y, w, h = (int(v) for v in roi_s.split(","))
            pitch = float(pitch_s)
        except Exception as exc:  # noqa: BLE001
            raise FiducialError(
                f"{ENV_FIDUCIAL} 형식 오류: {spec!r} — "
                '"간격mm:x,y,폭,높이" 예) "10:0,0,4056,220"'
            ) from exc
        if pitch <= 0 or w <= 0 or h <= 0:
            raise FiducialError(f"{ENV_FIDUCIAL} 값이 0 이하: {spec!r}")
        return FiducialConfig(pitch_mm=pitch, roi=(x, y, w, h))


def resolve_config() -> Optional["FiducialConfig"]:
    """환경변수에서 기준자 설정을 읽는다. 미설정이면 None.

    **설정이 틀렸을 때는 None 이 아니라 예외**다. 오타 하나로 조용히 저장된
    스케일로 떨어지면, 정확도가 왜 안 나오는지 현장에서 알 길이 없다.
    """
    spec = os.getenv(ENV_FIDUCIAL, "").strip()
    if not spec:
        return None
    return FiducialConfig.parse(spec)


class FiducialError(RuntimeError):
    """기준자를 못 읽었다. 호출자는 저장된 스케일로 떨어지되 **기록은 남긴다** —
    조용히 떨어지면 정확도가 왜 나빠졌는지 나중에 알 수 없다."""


@dataclass(frozen=True)
class FiducialScale:
    mm_per_px: float
    centers_px: List[float]
    """검출된 마크 중심의 측정축 좌표(오름차순)."""
    pitch_px: float
    """적합으로 얻은 마크 간 평균 간격(px)."""
    residual_px: float
    """등간격 적합 잔차 RMS. 크면 마크를 잘못 짚었거나 기준자가 휘었다."""

    @property
    def n_marks(self) -> int:
        return len(self.centers_px)

    def position_to_mm(self, x_px: float) -> float:
        """px 좌표를 기준자 **눈금 mm** 로 바꾼다 — 곱셈이 아니라 자 읽기.

        `mm_per_px` 한 값을 곱하는 것과 무엇이 다른가: 곱셈은 화면 전체의
        배율이 하나라고 가정한다. 실제 렌즈는 그렇지 않다 — 보정 후에도 잔차가
        위치에 따라 남고, 끝단은 하필 화면 가장자리에 있다.

        마크 사이를 선형보간하면 **끝단 바로 옆 마크 두 개** 로 그 자리의 배율을
        쓰게 되므로, 천천히 변하는 왜곡 잔차·원근이 함께 지워진다. 비텔레센트릭
        렌즈로 정밀 계측을 할 때 쓰는 표준 수법이고, 기준자를 이미 찍고 있으니
        추가 비용이 0 이다.

        마크 바깥은 끝 구간의 기울기로 외삽한다 — **기준자는 제품 양 끝보다
        길어야 한다.** 짧으면 외삽 구간에서 이 이점이 사라진다.
        """
        xs = self.centers_px
        n = len(xs)
        if n < 2:
            raise FiducialError("보간하려면 마크가 2개 이상")
        pitch_mm = self.mm_per_px * self.pitch_px
        if x_px <= xs[0]:
            seg = xs[1] - xs[0]
            return (x_px - xs[0]) / seg * pitch_mm if seg > 0 else 0.0
        if x_px >= xs[-1]:
            seg = xs[-1] - xs[-2]
            base = (n - 1) * pitch_mm
            return base + (x_px - xs[-1]) / seg * pitch_mm if seg > 0 else base
        i = int(np.searchsorted(xs, x_px)) - 1
        i = max(0, min(n - 2, i))
        seg = xs[i + 1] - xs[i]
        frac = (x_px - xs[i]) / seg if seg > 0 else 0.0
        return (i + frac) * pitch_mm

    def span_mm(self, x_left: float, x_right: float) -> float:
        """두 px 좌표 사이의 거리(mm). 측정은 이걸 쓴다."""
        return self.position_to_mm(x_right) - self.position_to_mm(x_left)

    @property
    def relative_sigma(self) -> float:
        """스케일의 상대 불확도 근사. 잔차를 전체 스팬으로 나눈 값.

        마크 N개 등간격 적합이므로 √N 만큼 좋아진다.
        """
        span = self.pitch_px * max(1, self.n_marks - 1)
        if span <= 0:
            return float("inf")
        return self.residual_px / span / math.sqrt(max(1, self.n_marks))


def _mark_centers(
    gray: np.ndarray, *, dark_marks: bool, min_area_px: int
) -> List[Tuple[float, float]]:
    """연결성분 중심. 측정축(x) 기준으로 정렬해 반환."""
    if cv2 is None:  # pragma: no cover
        raise FiducialError("OpenCV 없음")
    g = gray if gray.ndim == 2 else cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
    flag = cv2.THRESH_BINARY_INV if dark_marks else cv2.THRESH_BINARY
    _, mask = cv2.threshold(g, 0, 255, flag + cv2.THRESH_OTSU)
    n, _, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
    out: List[Tuple[float, float]] = []
    for i in range(1, n):   # 0 은 배경
        if stats[i, cv2.CC_STAT_AREA] < min_area_px:
            continue
        out.append((float(cents[i][0]), float(cents[i][1])))
    out.sort(key=lambda p: p[0])
    return out


def _fit_pitch(xs: Sequence[float]) -> Tuple[float, float]:
    """등간격 가정으로 index→위치 직선 적합 → (pitch_px, residual_rms).

    끝 두 개만 쓰지 않는 이유: 마크가 N개면 적합이 노이즈를 1/√N 로 줄인다.
    끝점 두 개만 쓰면 그 두 점의 노이즈가 그대로 스케일에 들어간다.
    """
    n = len(xs)
    idx = np.arange(n, dtype=np.float64)
    x = np.asarray(xs, dtype=np.float64)
    idx_c = idx - idx.mean()
    denom = float(np.dot(idx_c, idx_c))
    if denom < 1e-9:
        raise FiducialError("마크가 1개뿐 — 간격을 못 구한다")
    pitch = float(np.dot(idx_c, x - x.mean()) / denom)
    pred = x.mean() + pitch * idx_c
    rms = float(np.sqrt(np.mean((x - pred) ** 2)))
    return pitch, rms


def measure_scale(
    image: np.ndarray,
    *,
    pitch_mm: float,
    roi: Optional[Tuple[int, int, int, int]] = None,
    dark_marks: bool = True,
    min_marks: int = 3,
    min_area_px: int = 20,
    max_residual_px: float = 1.5,
) -> FiducialScale:
    """기준자 ROI 에서 마크를 읽어 이 프레임의 mm/px 을 구한다.

    roi: (x, y, w, h). 생략하면 전체 이미지 — 제품까지 마크로 집어올 수 있으니
      운영에서는 반드시 기준자 띠만 지정한다.
    pitch_mm: 이웃한 마크 사이의 **실제** 거리(mm).
    """
    if pitch_mm <= 0:
        raise FiducialError("pitch_mm 이 0 이하")
    img = image
    if roi is not None:
        x, y, w, h = roi
        if w <= 0 or h <= 0:
            raise FiducialError("기준자 ROI 가 비었다")
        img = image[y : y + h, x : x + w]
        if img.size == 0:
            raise FiducialError("기준자 ROI 가 이미지 밖")

    cents = _mark_centers(img, dark_marks=dark_marks, min_area_px=min_area_px)
    if len(cents) < min_marks:
        raise FiducialError(
            f"마크 {len(cents)}개 — 최소 {min_marks}개 필요. 조명/ROI 확인"
        )
    xs = [c[0] for c in cents]
    pitch_px, rms = _fit_pitch(xs)
    if pitch_px <= 0:
        raise FiducialError("마크 간격이 0 이하")
    if rms > max_residual_px:
        # 조용히 쓰면 틀린 스케일로 전수 오판이 난다. 차라리 실패시킨다.
        raise FiducialError(
            f"등간격 적합 잔차 {rms:.2f}px > {max_residual_px}px — 마크 오검출 의심"
        )
    return FiducialScale(
        mm_per_px=pitch_mm / pitch_px,
        centers_px=xs,
        pitch_px=pitch_px,
        residual_px=rms,
    )


#: 치수 측정의 국제 기준 온도. 도면 공차는 이 온도에서의 값이다.
REFERENCE_TEMP_C = 20.0
#: 알루미늄 선팽창계수 [1/K].
CTE_ALUMINIUM = 23.1e-6


def to_reference_temperature(
    length_mm: float,
    temp_c: float,
    *,
    cte_per_k: float = CTE_ALUMINIUM,
    reference_c: float = REFERENCE_TEMP_C,
) -> float:
    """측정 온도에서 잰 길이를 기준온도(20°C) 환산값으로 바꾼다.

    알루미늄 250mm 는 **1K 에 5.8µm** 움직인다. ±0.1mm 공차에서 10K 차이는
    0.058mm — 공차의 29% 다. 절단 직후 톱 발열이 남아 있으면 더 크다.

    환산 자체는 한 줄이지만, **온도를 모르면 못 한다.** 온도계가 없다면 이
    함수를 쓰지 말고 오차 예산에 열팽창 항을 그대로 남겨 두는 편이 정직하다.
    """
    denom = 1.0 + cte_per_k * (temp_c - reference_c)
    if denom <= 0:
        raise ValueError("비현실적인 온도")
    return length_mm / denom
