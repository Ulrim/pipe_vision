"""끝단 서브픽셀 위치 — 길이 측정이 쓰는 **단 하나의** 추정기(2026-10-20).

세로 평균 프로파일(`measure._find_edges`)과 행별 직선 적합(`edges._row_subpixel`)이 모두
이 함수로 끝단을 잰다. 둘이 다른 추정기를 쓰면 적합이 프로파일을 덮어쓸 때 값이 튄다.

방식 — **대칭 창 기울기 무게중심**:
  0) 행 방향 3점 중앙값. 단조 계단(끝단)은 **한 화소도 바꾸지 않고** 1px 짜리 튐(끝단 선
     반짝임·먼지·핫픽셀)만 지운다. 종전 경로는 반사 보정본의 3×3 미디언이 같은 일을 했다.
  1) 중앙차분 기울기에서 끝단 방향(상승/하강)과 반대 부호는 0 으로 버린다. 테두리 반짝임·
     후광의 되돌아오는 경사가 끝단 위치를 끌고 가지 못하게.
  2) 극값 ±1 에서 3점 포물선으로 첫 추정.
  3) 추정 위치를 **중심으로** 한 ±WINDOW_HALF_PX 창(가장자리 화소는 겹친 비율만큼)의 기울기
     무게중심을 다시 추정으로 삼아 CENTROID_ITERS 회 반복한다.
     블러가 대칭이면 기울기(선 퍼짐 함수)도 끝단에 대해 대칭이라, 끝단에 중심을 둔 창의
     무게중심은 끝단 그 자체다. 창을 정수 화소에 고정하면 끝단이 화소 안 어디냐에 따라 한쪽만
     잘려 위상 따라 흔들린다(S자) — 창을 추정에 따라 옮기는 것이 핵심이다. 끝단이 화소 하나에
     걸친 날카로운 계단은 중앙차분이 정확히 [f/2, 1/2, (1-f)/2] 라 무게중심이 끝단과 같다.

후보 10종(현행 둘 포함) 비교 — `vision.tools.subpixel_benchmark`, 결과 `models/benchmarks/SUBPIXEL_RESULT.md`:
위상 따라 흔들리는 치우침이 블러 σ 0.5~2px 에서 종전 0.031~0.050px(행별 포물선) →
0.005~0.020px(8비트 양자화 포함 — 양자화를 빼면 ≤0.003px), 노이즈 σ 는 블러 σ≥0.8 에서
더 작고(σ2 에서 0.46→0.17px), 1px 넘게 틀리는 행이 없다(종전 σ2·노이즈5 에서 7.5%).

입력은 **밝기**(그레이) — 기울기가 아니다. idx 는 기울기 극값의 정수 위치이고 다른 영상
(반사 보정본)에서 찾은 값이어도 된다(±1 에서 극값을 다시 찾고, 결과는 idx ±CLAMP_PX 로 묶는다).
"""
from __future__ import annotations

from typing import Optional

import numpy as np

#: 무게중심 창 반폭(px). 창 = 추정 ±(이 값 + 0.5). 블러 σ≈2px 의 기울기까지 대칭으로 담는다.
WINDOW_HALF_PX = 3.0
#: 창 재중심 반복 횟수(고정 — 결정성). 창이 기울기를 다 담으면 1회로 끝난다. 블러 σ=2px 에
#: 노이즈가 있으면 1회는 창이 덜 따라와 σ 0.23px, 2회부터 0.16px 로 3·4회와 같다(벤치마크).
CENTROID_ITERS = 2
#: 결과를 정수 끝단 ±이만큼(px)으로 묶는다 — 옆 구조물로 미끄러지지 않게.
CLAMP_PX = 2

_W = int(WINDOW_HALF_PX)
# 창 [x−W−0.5, x+W+0.5] 와 겹치는 화소는 floor(x) + (−W … W+1) 의 2W+2 개다. 끝단 위치의
# 소수부를 f 라 하면 겹친 비율은 맨 앞 1−f, 맨 뒤 f, 나머지 1 — 가중치 = _BASE + f·_TILT.
_OFFS = np.arange(-_W, _W + 2, dtype=np.float64)
_BASE = np.r_[np.ones(2 * _W + 1), 0.0]
_TILT = np.r_[-1.0, np.zeros(2 * _W), 1.0]
_OFFS_I = _OFFS.astype(np.intp)
#: 추정기가 정수 끝단에서 좌우로 읽는 최대 거리(px) — 묶음 2 + 창 W+1 + 중앙차분 1 + 중앙값 1.
#: 호출자는 이만큼의 이웃을 넘기면 창 경계에서 끝값을 덧대지 않고 실제 밝기로 잰다.
CONTEXT_PX = CLAMP_PX + _W + 1 + 1 + 1
_TAPS = np.arange(-CONTEXT_PX, CONTEXT_PX + 1)
_G0 = CONTEXT_PX - 2          # 중앙값·중앙차분 뒤 배열에서 정수 끝단(오프셋 0)의 인덱스


def _median3(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
    """원소별 세 값의 중앙값. np.median 보다 수십 배 빠르다(행마다 불린다)."""
    return np.maximum(np.minimum(a, b), np.minimum(np.maximum(a, b), c))


def edge_subpixel_rows(rows: np.ndarray, idx: np.ndarray) -> np.ndarray:
    """행마다 끝단 서브픽셀 x(행 로컬 좌표). 실패한 행은 NaN.

    rows: (R, W) 밝기. idx: (R,) 정수 끝단(기울기 극값) 위치, 0 <= idx < W.
    상승·하강은 idx 주변 기울기 부호로 정한다. 좌표계는 화소 중심이 정수(화소 k 가
    [k-0.5, k+0.5] 를 덮는다) — 종전 포물선과 같다. 창 밖은 끝값으로 본다.
    결정적이다(난수·수렴 판정 없음, 고정 횟수). 행마다 불리므로 정수 끝단 ±CONTEXT_PX 만
    떼어 와서 계산한다 — 창 전체를 다루면 그 고정비가 곧 처리시간이다.
    """
    rows = np.asarray(rows)
    if rows.ndim != 2 or rows.shape[1] < 3:
        raise ValueError("edge_subpixel_rows: (R, W>=3) 배열이 필요하다")
    idx = np.asarray(idx, dtype=np.intp).reshape(-1)
    n, w = rows.shape
    if idx.shape[0] != n:
        raise ValueError("edge_subpixel_rows: idx 길이가 행 수와 다르다")
    if n == 0:
        return np.empty(0)
    if idx.min() < 0 or idx.max() >= w:
        raise ValueError("edge_subpixel_rows: idx 가 창 밖이다")
    r = np.arange(n)[:, None]
    # 정수 끝단 ±CONTEXT_PX 만(창 밖은 끝값) → 3점 중앙값 → 중앙차분(×2 — 무게중심엔 무관).
    p = rows[r, np.clip(idx[:, None] + _TAPS, 0, w - 1)].astype(np.float64)
    m = _median3(p[:, :-2], p[:, 1:-1], p[:, 2:])
    g = m[:, 2:] - m[:, :-2]               # g[:, _G0 + d] = 정수 끝단 + d 의 기울기
    near = g[:, _G0 - 1:_G0 + 2]                                     # 극값 ±1
    polarity = np.sign(near.sum(axis=1))
    g = np.maximum(g * polarity[:, None], 0.0)                       # 반대 부호는 버린다
    near = near * polarity[:, None]
    # 첫 추정: 극값 ±1 에서 다시 찾은 정점 + 3점 포물선(보정량 ±1 넘으면 버림).
    j = _G0 - 1 + np.argmax(near, axis=1)
    y0, y1, y2 = g[r[:, 0], j - 1], g[r[:, 0], j], g[r[:, 0], j + 1]
    den = y0 - 2.0 * y1 + y2
    delta = 0.5 * (y0 - y2) / np.where(den < 0, den, -1.0)            # 정점이면 den<0
    x = j + np.where((den < 0) & (np.abs(delta) <= 1.0), delta, 0.0)
    total = np.ones(n)
    # 추정을 중심으로 한 대칭 창의 무게중심. 가장자리 화소는 창과 겹친 비율만큼 센다.
    for _ in range(CENTROID_ITERS):
        base = np.floor(x)
        gw = g[r, base.astype(np.intp)[:, None] + _OFFS_I] * (_BASE + (x - base)[:, None] * _TILT)
        total = gw.sum(axis=1)
        x = base + (gw @ _OFFS) / np.where(total > 0, total, 1.0)
        x = np.minimum(np.maximum(x, _G0 - CLAMP_PX), _G0 + CLAMP_PX)
    bad = (polarity == 0) | ~(total > 0)
    return np.where(bad, np.nan, idx + (x - _G0))


def edge_subpixel(signal: np.ndarray, idx: int) -> Optional[float]:
    """1D 밝기 신호의 끝단 서브픽셀 위치. 실패하면 None — `edge_subpixel_rows` 의 1행판."""
    s = np.asarray(signal, dtype=np.float64).reshape(1, -1)
    if s.shape[1] < 3 or not 0 <= int(idx) < s.shape[1]:
        return None
    x = float(edge_subpixel_rows(s, np.array([int(idx)]))[0])
    return x if np.isfinite(x) else None
