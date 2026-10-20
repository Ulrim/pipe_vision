"""경량 비지도 이상탐지 (PaDiM-lite / Mahalanobis) — CLAUDE.md §6.3.

정상(OK) 이미지만으로 학습한 정상 분포(평균 벡터 + 정규화 공분산의 역행렬)에서
표면 기술자의 Mahalanobis 거리를 계산해 "정상 분포 이탈"을 탐지한다. Anomalib
(PatchCore/PaDiM/EfficientAD)의 핵심 아이디어를 라즈베리파이4(ARM CPU, GPU 없음)
에서 실제로 도는 경량 형태로 옮긴 것으로, **numpy/opencv 만** 사용한다
(torch/onnxruntime 불필요, 결정적). 하드웨어를 올리면(예: Jetson) 동일한
SurfaceModel 인터페이스에 Anomalib-ONNX 백엔드만 갈아끼우면 된다.

정책(§6.3 "동작하는 폴백 → 점진 고도화", §5 M5):
- predict 는 **항상 먼저 고전 CV(analyze_surface)** 로 oil/discolor/scratch 점수·
  코드·verdict 를 산출한다(named 코드/기존 동작 보존, 미판정 0 유지).
- 학습된 npz 모델이 있으면 기술자→Mahalanobis→이상점수(0~1, 학습 임계 대비)를
  계산한다. 이상점수가 학습 임계 이상이면 **재확인 대상(review)** 으로만 표시한다.
  이상탐지만으로 final NG 를 강제하지 않는다(미학습 초기 오검 방지 — 사람 재확인
  유도가 정직한 1차 동작). classical 코드가 있으면 기존대로 NG 를 유지한다.
- 모델이 없으면 classical 결과를 그대로 반환한다(자동검사율 100%).
- anomaly_score/review 는 공유 스키마(SurfaceResult)를 바꾸지 않기 위해 별도
  채널(last_report)로 노출한다. 파이프라인이 이를 VerdictResult.review_flag 에 OR
  한다(스키마·DB 미변경).

임계·정칙화계수는 하드코딩하지 않는다(학습 npz + 인자에서 온다).

기술자 세트(npz `feature_set`):
- `v1`(구 모델, 키 없음): 영역 통계 19개(분위수 포함), 정사각 격자, 최악 패치.
  예전에 학습한 npz 는 이 경로로 **예전과 똑같이** 채점한다.
- `v2`(새 모델 기본): 질감 에너지 15개(국소 대비·DoG 대역·방향 그래디언트·색)를
  화소 맵에서 한 번 계산해 패치로 **평균 풀링**하고 에너지는 로그로 편다. 패치는
  짧은 변 기준 정사각형에 가깝게 깔고(가늘고 긴 튜브 crop 대응) 반씩 겹친다.
  이미지 점수는 상위 k 패치 평균. 근거는 DAGM 벤치마크
  (vision/models/benchmarks/DAGM_RESULT.md).
"""
from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from aivis_types import ItemMaster, SurfaceResult

from .classical import analyze_surface
from .model import ClassicalSurfaceModel, SurfaceModel

#: 패치 한 변의 최소 화소. 이보다 작으면 분위수·표준편차가 불안정해진다.
_MIN_PATCH_PX = 32

#: 통계를 낼 때 쓰는 최대 화소 표본 수. 이보다 많으면 일정 간격으로 솎아 쓴다.
#:
#: **이미지를 줄이지 않고 표본만 줄인다.** 처음에는 영역 자체를 축소했는데 가는
#: 결함이 뭉개져 DAGM 8x8 AUROC 가 크게 떨어졌다(당시 측정: 1.000→0.708). 파생 맵
#: (그래디언트·라플라시안·Canny)은 원본 해상도에서 계산해야 가는 스크래치가
#: 살아남는다. 반면 그 맵들의 **통계**(평균·표준편차·분위수)는 화소를 전부 볼
#: 필요가 없다 — 5만 표본이면 64만 표본과 사실상 같은 값이 나온다.
#:
#: 비용 구조 실측(1600x400): 맵 계산 13ms, 통계 78ms. 비싼 쪽은 통계였다.
_MAX_STAT_PX = int(os.getenv("AIVIS_ANOMALY_MAX_STAT_PX", "50000") or 50000)

# 고정 차원 기술자(결정적). 순서를 바꾸면 기존 npz 와 호환 불가 → version 관리.
FEATURE_NAMES = (
    "L_mean",
    "L_std",
    "a_mean",
    "a_std",
    "b_mean",
    "b_std",
    "grad_mean",
    "grad_std",
    "grad_p95",
    "lap_std",
    "sat_ratio",
    "tophat_ratio",
    "edge_density",
    "colorfulness",
    "ab_dist_mean",
    "ab_dist_p95",
    "gray_p10",
    "gray_p50",
    "gray_p90",
)
FEATURE_DIM = len(FEATURE_NAMES)

#: 기술자 세트 이름(npz `feature_set`). 키가 없는 구 모델은 v1 이다.
FEATURE_SET_V1 = "v1"
FEATURE_SET_V2 = "v2"
FEATURE_SETS = (FEATURE_SET_V1, FEATURE_SET_V2)

# v2 기술자(결정적). 순서를 바꾸면 기존 v2 npz 와 호환 불가.
FEATURE_NAMES_V2 = (
    "lc_mean",     # 국소 대비(7x7 표준편차) 평균 — 질감 세기
    "lc_std",      # 국소 대비의 패치 내 퍼짐 — 질감 세기가 고르지 않은 곳
    "dog1_mean",   # 대역 에너지 |I−G(σ1)| — 가는 구조
    "dog2_mean",   # 대역 에너지 |G(σ1)−G(σ2)|
    "dog4_mean",   # 대역 에너지 |G(σ2)−G(σ4)| — 굵은 얼룩
    "grad_mean",   # 그래디언트 크기 평균
    "grad_std",    # 그래디언트 크기 퍼짐
    "grad_0",      # 방향 에너지 |∂x|
    "grad_90",     # 방향 에너지 |∂y|
    "grad_45",     # 방향 에너지 |∂x+∂y|/√2
    "grad_135",    # 방향 에너지 |∂x−∂y|/√2
    "a_mean",      # 색(Lab a) — 변색
    "b_mean",      # 색(Lab b)
    "a_std",
    "b_std",
)
FEATURE_DIM_V2 = len(FEATURE_NAMES_V2)
#: 앞에서부터 이 개수까지가 에너지(양수·오른쪽 꼬리) → log1p 로 편다.
#:
#: 에너지는 조명 세기에 곱으로 따라 움직이고 분포가 오른쪽으로 길다. 가우시안
#: (Mahalanobis)은 꼬리를 정상 변동으로 크게 잡아 민감도를 잃는다 — 로그를 씌우면
#: 곱이 합이 되고 분포가 대칭에 가까워진다. DAGM 교차검증 AUROC 0.950→0.990(탐색).
_V2_LOG_DIM = 11

#: **새로 학습하는 모델**의 기본값(DAGM 벤치마크로 고름 — DAGM_RESULT.md).
#: 예전 npz 는 파일에 적힌 값(없으면 v1·격자 1·겹침 없음·최악 패치)을 따른다.
DEFAULT_FEATURE_SET = FEATURE_SET_V2
DEFAULT_PATCH_GRID = 8
DEFAULT_PATCH_OVERLAP = True
DEFAULT_SCORE_TOPK = 2

#: v2 패치를 채점에 넣는 최소 전경 비율. 대부분 배경인 패치는 통계가 몇 화소에서
#: 나와 들쭉날쭉하다 — 정상품을 이상으로 몰지 않게 뺀다.
_MIN_PATCH_FG = 0.5


def resolve_anomaly_model_path(
    item_code: Optional[str], model_path: Optional[str] = None
) -> Optional[str]:
    """이상탐지 npz 모델 경로 결정(하드코딩 금지 — env/인자/품목).

    우선순위: 명시 인자 > AIVIS_SURFACE_ANOMALY_MODEL(env) >
    services/vision/models/anomaly_<item_code>.npz. 존재하지 않으면 None.
    """
    cand = model_path or os.environ.get("AIVIS_SURFACE_ANOMALY_MODEL")
    if cand:
        return cand if Path(cand).exists() else None
    if item_code:
        default = (
            Path(__file__).resolve().parents[1]
            / "models"
            / f"anomaly_{item_code}.npz"
        )
        return str(default) if default.exists() else None
    return None


def _foreground_mask(
    region_bgr: np.ndarray, mask: Optional[np.ndarray]
) -> np.ndarray:
    """표면 영역 내 전경 마스크(bool). mask 미제공 시 전체 True."""
    h, w = region_bgr.shape[:2]
    if mask is None:
        return np.ones((h, w), dtype=bool)
    m = mask
    if m.shape[:2] != (h, w):
        m = cv2.resize(m, (w, h), interpolation=cv2.INTER_NEAREST)
    return m > 0


@dataclass
class _Maps:
    """영역 전체에서 **한 번만** 계산한 파생 맵들.

    기술자 19개는 전부 이 맵들의 통계다. 패치마다 cvtColor/Sobel/Laplacian/
    top-hat/Canny 를 다시 돌리면 8x8 격자에서 같은 연산을 64번 반복하게 된다
    (실측: 1600x400 영역에서 141ms — 라즈베리파이4 로는 300ms 예산을 넘긴다).
    맵을 한 번 만들고 패치는 **잘라서 통계만** 낸다.

    덤으로 정확도에도 유리하다. 패치별로 Sobel/Canny 를 돌리면 패치 경계마다
    인위적인 에지가 생겨 없는 결함을 만들어낸다. 전체에서 계산하면 그 경계가 없다.
    """

    gray: np.ndarray
    L: np.ndarray
    A: np.ndarray
    B: np.ndarray
    gmag: np.ndarray
    lap: np.ndarray
    tophat: np.ndarray
    edges: np.ndarray
    rg: np.ndarray
    yb: np.ndarray
    fg: np.ndarray
    fg_er: np.ndarray


def _compute_maps(
    region_bgr: np.ndarray, mask: Optional[np.ndarray] = None
) -> Optional[_Maps]:
    """파생 맵 일괄 계산. 전경이 없으면 None(호출자가 0 벡터 처리)."""
    fg = _foreground_mask(region_bgr, mask)
    if int(fg.sum()) == 0:
        return None

    # 텍스처/에지용: 경계 에지 배제를 위해 전경 침식.
    fg_u8 = (fg.astype(np.uint8)) * 255
    er = cv2.erode(
        fg_u8, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)), iterations=1
    )
    fg_er = er > 0
    if int(fg_er.sum()) < 10:
        fg_er = fg  # 침식이 전경을 지우면 원본 전경 사용.

    gray = cv2.cvtColor(region_bgr, cv2.COLOR_BGR2GRAY)
    lab = cv2.cvtColor(region_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)

    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    gmag = np.sqrt(gx * gx + gy * gy)

    lap = cv2.Laplacian(gray, cv2.CV_32F, ksize=3)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
    tophat = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kernel)

    g0 = gray.copy()
    g0[~fg] = 0
    edges = cv2.Canny(g0, 60, 160)

    Bc = region_bgr[:, :, 0].astype(np.float32)
    Gc = region_bgr[:, :, 1].astype(np.float32)
    Rc = region_bgr[:, :, 2].astype(np.float32)

    return _Maps(
        gray=gray,
        L=lab[:, :, 0],
        A=lab[:, :, 1],
        B=lab[:, :, 2],
        gmag=gmag,
        lap=lap,
        tophat=tophat,
        edges=edges,
        rg=Rc - Gc,
        yb=0.5 * (Rc + Gc) - Bc,
        fg=fg,
        fg_er=fg_er,
    )


def _stats_from_maps(m: _Maps, box: Optional[tuple] = None) -> np.ndarray:
    """맵의 한 구간(box=(y0,y1,x0,x1), None=전체)에서 기술자 19개를 낸다."""
    if box is None:
        sl = (slice(None), slice(None))
    else:
        y0, y1, x0, x1 = box
        sl = (slice(y0, y1), slice(x0, x1))

    fg = m.fg[sl]
    n_fg = int(fg.sum())
    if n_fg == 0:
        return np.zeros(FEATURE_DIM, dtype=np.float64)
    fg_er = m.fg_er[sl]
    if int(fg_er.sum()) < 10:
        fg_er = fg  # 패치가 작아 침식분이 비면 전경 사용.

    # 표본이 너무 많으면 **배열 뷰를 간격으로 잘라** 솎는다. 인덱스 배열을 만드는
    # 방식(flatnonzero)보다 훨씬 싸다 — 뷰는 복사가 없다. 간격 추출이라 결정적이다
    # (무작위 표본은 실행마다 값이 바뀐다).
    step = 1
    if _MAX_STAT_PX > 0 and n_fg > _MAX_STAT_PX:
        step = int(math.ceil(math.sqrt(n_fg / float(_MAX_STAT_PX))))
    if step > 1:
        ss = (slice(sl[0].start, sl[0].stop, step), slice(sl[1].start, sl[1].stop, step))
        fg = fg[::step, ::step]
        fg_er = fg_er[::step, ::step]
    else:
        ss = sl

    def _sel(arr: np.ndarray, m_: np.ndarray) -> np.ndarray:
        return arr[ss][m_]

    Lf, Af, Bf = _sel(m.L, fg), _sel(m.A, fg), _sel(m.B, fg)
    gmag_f = _sel(m.gmag, fg_er)
    lap_f = _sel(m.lap, fg_er)
    gray_f_u8 = _sel(m.gray, fg)

    sat_ratio = float(np.mean(gray_f_u8 >= 245))
    tophat_ratio = float(np.mean(_sel(m.tophat, fg) >= 40))
    edge_density = float(np.mean(_sel(m.edges, fg_er) > 0))

    rg = _sel(m.rg, fg)
    yb = _sel(m.yb, fg)
    colorfulness = float(
        math.sqrt(float(rg.std()) ** 2 + float(yb.std()) ** 2)
        + 0.3 * math.sqrt(float(rg.mean()) ** 2 + float(yb.mean()) ** 2)
    )

    a_med, b_med = float(np.median(Af)), float(np.median(Bf))
    ab_dist = np.sqrt((Af - a_med) ** 2 + (Bf - b_med) ** 2)

    gray_f = gray_f_u8.astype(np.float32)
    gp10, gp50, gp90 = (float(np.percentile(gray_f, q)) for q in (10, 50, 90))

    vec = np.array(
        [
            float(Lf.mean()),
            float(Lf.std()),
            float(Af.mean()),
            float(Af.std()),
            float(Bf.mean()),
            float(Bf.std()),
            float(gmag_f.mean()),
            float(gmag_f.std()),
            float(np.percentile(gmag_f, 95)),
            float(lap_f.std()),
            sat_ratio,
            tophat_ratio,
            edge_density,
            colorfulness,
            float(ab_dist.mean()),
            float(np.percentile(ab_dist, 95)),
            gp10,
            gp50,
            gp90,
        ],
        dtype=np.float64,
    )
    return np.nan_to_num(vec, nan=0.0, posinf=0.0, neginf=0.0)


def extract_descriptor(
    region_bgr: np.ndarray, mask: Optional[np.ndarray] = None
) -> np.ndarray:
    """표면 ROI → 고정 차원(FEATURE_DIM) 기술자 벡터(결정적, float64).

    전경(fg) 내부에서만 통계를 낸다(배경 배제). 텍스처/에지 통계는 전경을 살짝
    침식(erode)해 ROI 경계 에지(파이프 vs 배경) 오염을 줄인다. 전경이 비면 0 벡터
    (미판정 0 안전장치).
    """
    if region_bgr is None or region_bgr.ndim != 3:
        return np.zeros(FEATURE_DIM, dtype=np.float64)
    h, w = region_bgr.shape[:2]
    if h < 3 or w < 3:
        return np.zeros(FEATURE_DIM, dtype=np.float64)
    maps = _compute_maps(region_bgr, mask)
    if maps is None:
        return np.zeros(FEATURE_DIM, dtype=np.float64)
    return _stats_from_maps(maps, None)


def patch_descriptors(
    region_bgr: np.ndarray,
    mask: Optional[np.ndarray] = None,
    *,
    grid: int = 1,
) -> np.ndarray:
    """표면 영역을 grid x grid 로 나눠 패치별 기술자 행렬(P x FEATURE_DIM)을 만든다.

    **왜 나누는가**: 기술자는 영역 전체의 평균·표준편차·분위수다. 영역이 넓으면
    작은 국소 결함(스크래치가 대표적)이 통계에 거의 영향을 주지 않아 정상과
    구분되지 않는다. DAGM 2007 벤치마크에서 실측했다 — 같은 기술자·같은 모델로
    전체를 한 벡터로 보면 AUROC 0.526(동전 던지기 수준, 결함 27개 중 26개 놓침),
    8x8 패치로 나눠 최악 패치로 채점하면 0.889(1개 놓침)였다.

    파생 맵은 **전체에서 한 번만** 계산하고 패치는 잘라서 통계만 낸다
    (_Maps 주석 참조 — 라즈베리파이4 예산 때문).

    grid=1 이면 기존 동작과 동일한 1행을 돌려준다(하위호환).
    패치가 너무 작으면 통계가 불안정하므로 최소 변 길이를 보장하고, 그보다
    작아지면 grid 를 자동으로 낮춘다.
    """
    g = max(1, int(grid))
    if region_bgr is None or region_bgr.ndim != 3 or region_bgr.size == 0:
        return np.zeros((1, FEATURE_DIM), dtype=np.float64)
    if region_bgr.shape[0] < 3 or region_bgr.shape[1] < 3:
        return np.zeros((1, FEATURE_DIM), dtype=np.float64)

    maps = _compute_maps(region_bgr, mask)
    if maps is None:
        return np.zeros((1, FEATURE_DIM), dtype=np.float64)
    # 격자·패치 크기는 **축소된** 맵 기준으로 잡는다.
    h, w = maps.gray.shape[:2]
    if g > 1:
        g = min(g, max(1, h // _MIN_PATCH_PX), max(1, w // _MIN_PATCH_PX))
    if g <= 1:
        return _stats_from_maps(maps, None).reshape(1, FEATURE_DIM)

    ph, pw = h // g, w // g
    rows: list[np.ndarray] = []
    for r in range(g):
        for c in range(g):
            y0, x0 = r * ph, c * pw
            # 마지막 행/열은 나머지 화소까지 포함(잘라 버리면 가장자리 결함을 놓친다).
            y1 = h if r == g - 1 else y0 + ph
            x1 = w if c == g - 1 else x0 + pw
            rows.append(_stats_from_maps(maps, (y0, y1, x0, x1)))
    return np.vstack(rows).astype(np.float64)


# --- v2: 질감 에너지 맵 + 평균 풀링 ------------------------------------------


def patch_layout(h: int, w: int, grid: int) -> tuple[int, int]:
    """v2 패치 배치 (행 수, 열 수).

    **짧은 변**을 grid 칸으로 나누고 긴 변은 패치가 정사각형에 가깝도록 칸 수를
    정한다. v1 의 grid x grid 는 가늘고 긴 튜브 crop(예: 1728x64)에서 짧은 변이
    최소 패치(32px)에 걸려 격자가 1로 떨어졌다 — 다발 검사에서는 격자를 아무리
    키워도 표면 전체를 한 덩어리로 봤다. 정사각형 DAGM(512x512)에서는 v1 과 같은
    grid x grid 가 된다. 한 변이 _MIN_PATCH_PX 보다 작아지지 않게 칸 수를 줄인다.
    """
    g = max(1, int(grid))
    short, long_ = (h, w) if h <= w else (w, h)
    n_s = max(1, min(g, short // _MIN_PATCH_PX))
    side = short / float(n_s)
    n_l = max(1, int(round(long_ / max(side, 1.0))))
    n_l = max(1, min(n_l, max(1, long_ // _MIN_PATCH_PX)))
    return (n_s, n_l) if h <= w else (n_l, n_s)


def _v2_cells(
    region_bgr: np.ndarray,
    rc: int,
    cc: int,
    bg_tex: Optional[np.ndarray],
    bg_col: Optional[np.ndarray],
) -> list[np.ndarray]:
    """v2 화소 맵을 하나씩 만들어 **곧바로** 칸 평균(rc x cc)으로 줄인다.

    맵 순서(고정): lc, lc², dog1, dog2, dog4, |∇|, |∇|², |∂x|, |∂y|, |∂x+∂y|,
    |∂x−∂y| (질감 11) · a, a², b, b² (색 4). 제곱 맵은 패치 표준편차
    √(E[x²]−E[x]²) 용. 45/135° 의 1/√2 는 풀링 뒤에 곱한다(평균은 선형).

    - 전부 **원본 해상도**에서 만든다. 가는 스크래치는 축소하면 뭉개진다
      (_MAX_STAT_PX 주석 참조). 분위수가 없어 비용은 패치 수가 아니라 화소 수에
      비례한다.
    - 맵을 다 쌓아 두고 나중에 줄이면 crop 하나에 맵 15장(1728x96 이면 10MB)이
      캐시를 밀어낸다. 만들자마자 줄이고 버린다.
    - 칸 평균은 INTER_AREA 축소(분수 화소 경계까지 면적 가중 — 결정적).
    - 마스크가 있으면 배경 화소를 0 으로 지운 뒤 줄인다 → 칸 평균 = sum(x·w)/칸면적
      (전경 비율로 나누는 것은 _v2_patches). 곱셈 대신 인덱스로 지운다 — 튜브 crop 은
      전경이 거의 전부라 지울 화소가 적다.
    """
    cells: list[np.ndarray] = []

    def push(x: np.ndarray, bg: Optional[np.ndarray]) -> None:
        if bg is not None and bg.size:
            np.put(x, bg, 0.0)
        p = cv2.resize(x, (cc, rc), interpolation=cv2.INTER_AREA)
        cells.append(p.reshape(rc, cc).astype(np.float64))

    gray = cv2.cvtColor(region_bgr, cv2.COLOR_BGR2GRAY)
    g = gray.astype(np.float32)

    # 국소 대비: 7x7 창의 표준편차(창 평균을 빼므로 큰 조명 기울기는 빠진다).
    m = cv2.blur(g, (7, 7))
    var = np.maximum(cv2.sqrBoxFilter(g, -1, (7, 7)) - m * m, 0.0, dtype=np.float32)
    push(np.sqrt(var), bg_tex)
    push(var, bg_tex)

    # 대역 에너지(DoG): 가는 구조 → 굵은 얼룩까지 세 대역.
    b1 = cv2.GaussianBlur(g, (0, 0), 1.0)
    push(cv2.absdiff(g, b1), bg_tex)
    b2 = cv2.GaussianBlur(g, (0, 0), 2.0)
    push(cv2.absdiff(b1, b2), bg_tex)
    b4 = cv2.GaussianBlur(g, (0, 0), 4.0)
    push(cv2.absdiff(b2, b4), bg_tex)

    # 그래디언트 크기 + 네 방향 에너지(스크래치는 방향이 있다). uint8→int16 Sobel 은
    # float 와 값이 같고(정수, |값|≤1020) 훨씬 빠르다.
    gx = cv2.Sobel(gray, cv2.CV_16S, 1, 0, ksize=3).astype(np.float32)
    gy = cv2.Sobel(gray, cv2.CV_16S, 0, 1, ksize=3).astype(np.float32)
    gm = cv2.magnitude(gx, gy)
    push(gm * gm, bg_tex)
    push(gm, bg_tex)
    push(np.abs(gx), bg_tex)
    push(np.abs(gy), bg_tex)
    push(cv2.absdiff(gx, -gy), bg_tex)  # |∂x+∂y|
    push(cv2.absdiff(gx, gy), bg_tex)   # |∂x−∂y|

    lab = cv2.cvtColor(region_bgr, cv2.COLOR_BGR2LAB)
    for ch in (1, 2):
        c = lab[:, :, ch].astype(np.float32)
        push(c * c, bg_col)
        push(c, bg_col)
    # push 순서 → 표준 순서(제곱이 값 뒤). gm/c 는 제곱을 먼저 넣어 원본을 덮어쓰지
    # 않게 했다(np.put 이 제자리 수정).
    lc, lc2, d1, d2, d4, gm2, gm_, ox, oy, od1, od2, a2, a, b2, b = cells
    return [lc, lc2, d1, d2, d4, gm_, gm2, ox, oy, od1, od2, a, a2, b, b2]


def _v2_patches(
    cells: list[np.ndarray],
    n_tex: Optional[np.ndarray],
    n_col: Optional[np.ndarray],
    overlap: bool,
    whole: bool = False,
) -> tuple[np.ndarray, np.ndarray]:
    """칸 평균 → 패치 기술자 (P x FEATURE_DIM_V2, 유효 패치 bool[P]).

    겹침이면 칸을 반 패치 크기로 잡았으므로 2x2 칸을 묶어 패치를 만든다
    (보폭 = 반 패치). whole=True 면 칸 전체를 한 패치로(영역 전체 1행 폴백 —
    칸 면적이 같으므로 칸 평균의 평균 = 영역 평균). n_tex/n_col(전경 비율의 칸
    평균)이 있으면 그것으로 나눠 전경만의 평균으로 바꾼다.
    """

    def agg(p: np.ndarray) -> np.ndarray:
        if whole:
            return np.array([p.mean()])
        if overlap:
            p = 0.25 * (p[:-1, :-1] + p[1:, :-1] + p[:-1, 1:] + p[1:, 1:])
        return p.ravel()

    nt = agg(n_tex) if n_tex is not None else None
    nc = agg(n_col) if n_col is not None else None
    vals = [agg(c) for c in cells]
    if nt is not None:
        den = np.maximum(nt, 1e-9)
        vals[:11] = [v / den for v in vals[:11]]
    if nc is not None:
        den = np.maximum(nc, 1e-9)
        vals[11:] = [v / den for v in vals[11:]]
    lc, lc2, d1, d2, d4, gm, gm2, ox, oy, od1, od2, a, a2, b, b2 = vals

    def std(m1: np.ndarray, m2: np.ndarray) -> np.ndarray:
        return np.sqrt(np.maximum(m2 - m1 * m1, 0.0))

    inv_sqrt2 = 1.0 / math.sqrt(2.0)
    feats = np.stack(
        [
            lc, std(lc, lc2), d1, d2, d4, gm, std(gm, gm2), ox, oy,
            od1 * inv_sqrt2, od2 * inv_sqrt2,
            a, b, std(a, a2), std(b, b2),
        ],
        axis=1,
    )
    feats[:, :_V2_LOG_DIM] = np.log1p(np.maximum(feats[:, :_V2_LOG_DIM], 0.0))
    feats = np.nan_to_num(feats, nan=0.0, posinf=0.0, neginf=0.0)

    valid = np.ones(feats.shape[0], dtype=bool)
    if nc is not None:
        valid &= nc >= _MIN_PATCH_FG
    if nt is not None:
        valid &= nt >= 0.5 * _MIN_PATCH_FG
    return feats, valid


def patch_features_v2(
    region_bgr: np.ndarray,
    mask: Optional[np.ndarray] = None,
    *,
    grid: int = DEFAULT_PATCH_GRID,
    overlap: bool = DEFAULT_PATCH_OVERLAP,
) -> np.ndarray:
    """표면 영역 → v2 패치 기술자 행렬(P x FEATURE_DIM_V2, 결정적, float64).

    화소 맵을 **한 번** 만들어 칸 평균으로 줄이고(_v2_cells), 패치는 칸을 묶어
    낸다(_v2_patches). 패치 수가 늘어도 비용이 거의 그대로라 겹친 패치(보폭 반
    패치)를 공짜에 가깝게 쓴다. 전경이 절반 미만인 패치는 뺀다. 남는 패치가 없으면
    영역 전체 1행, 전경이 아예 없으면 0 벡터 1행(검사 루프가 멈추면 안 된다 —
    v1 과 같은 계약).
    """
    zero = np.zeros((1, FEATURE_DIM_V2), dtype=np.float64)
    if region_bgr is None or region_bgr.ndim != 3 or region_bgr.size == 0:
        return zero
    h, w = region_bgr.shape[:2]
    if h < 3 or w < 3:
        return zero

    rows, cols = patch_layout(h, w, grid)
    k = 2 if overlap else 1
    rc, cc = rows * k, cols * k

    n_tex = n_col = None
    bg_tex = bg_col = None
    if mask is not None:
        fg = _foreground_mask(region_bgr, mask)
        if int(fg.sum()) == 0:
            return zero
        if not bool(fg.all()):
            # 질감은 v1 처럼 전경을 살짝 깎아 제품-배경 경계 에지를 뺀다.
            er = cv2.erode(
                fg.astype(np.uint8) * 255,
                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
                iterations=1,
            )
            fg_er = er > 0
            if int(fg_er.sum()) < 10:
                fg_er = fg
            bg_col = np.flatnonzero(~fg)
            bg_tex = np.flatnonzero(~fg_er)

            def frac(b: np.ndarray) -> np.ndarray:
                p = cv2.resize(b.astype(np.float32), (cc, rc), interpolation=cv2.INTER_AREA)
                return p.reshape(rc, cc).astype(np.float64)

            n_col, n_tex = frac(fg), frac(fg_er)

    cells = _v2_cells(region_bgr, rc, cc, bg_tex, bg_col)
    feats, valid = _v2_patches(cells, n_tex, n_col, overlap)
    if bool(valid.any()):
        return feats[valid]
    whole, _ = _v2_patches(cells, n_tex, n_col, overlap, whole=True)
    return whole


def patch_features(
    region_bgr: np.ndarray,
    mask: Optional[np.ndarray] = None,
    *,
    feature_set: str = DEFAULT_FEATURE_SET,
    grid: int = DEFAULT_PATCH_GRID,
    overlap: bool = DEFAULT_PATCH_OVERLAP,
) -> np.ndarray:
    """기술자 세트별 패치 기술자 — 학습·추론·벤치마크가 같은 함수를 쓴다.

    v1 은 patch_descriptors 그대로(겹침 인자는 무시 — 구 모델 동작 보존).
    """
    if feature_set == FEATURE_SET_V1:
        return patch_descriptors(region_bgr, mask, grid=grid)
    if feature_set == FEATURE_SET_V2:
        return patch_features_v2(region_bgr, mask, grid=grid, overlap=overlap)
    raise ValueError(f"알 수 없는 기술자 세트: {feature_set!r} (허용: {FEATURE_SETS})")


def feature_dim(feature_set: str) -> int:
    """기술자 세트의 차원."""
    if feature_set == FEATURE_SET_V1:
        return FEATURE_DIM
    if feature_set == FEATURE_SET_V2:
        return FEATURE_DIM_V2
    raise ValueError(f"알 수 없는 기술자 세트: {feature_set!r} (허용: {FEATURE_SETS})")


def image_score(distances: np.ndarray, topk: int = 1) -> float:
    """패치 거리 → 이미지 점수: 상위 k 개 평균(k=1 이면 최악 패치 = v1).

    겹친 패치에서 실제 결함은 이웃 패치 여러 개에 함께 걸리지만, 정상 표면의
    우연한 튐은 한 패치에만 걸린다. 상위 2개 평균은 앞쪽을 거의 깎지 않고 뒤쪽을
    눌러 준다(DAGM: 겹침+top2 가 겹침+최악 패치보다 고정 오검률 재현율이 높았다).
    겹침 없이 쓰면 오히려 손해다 — 작은 결함은 한 패치에만 있다.
    """
    d = np.asarray(distances, dtype=np.float64).ravel()
    if d.size == 0:
        return 0.0
    k = max(1, min(int(topk), d.size))
    if k == 1:
        return float(d.max())
    return float(np.sort(d)[-k:].mean())


def mahalanobis_distance(
    x: np.ndarray, mean: np.ndarray, cov_inv: np.ndarray
) -> float:
    """Mahalanobis 거리 sqrt((x-μ)ᵀ Σ⁻¹ (x-μ)). 결정적, 음수 클립."""
    d = (x.astype(np.float64) - mean.astype(np.float64))
    m2 = float(d @ cov_inv @ d)
    if not math.isfinite(m2) or m2 < 0.0:
        m2 = max(0.0, m2) if math.isfinite(m2) else 0.0
    return math.sqrt(m2)


def mahalanobis_rows(
    X: np.ndarray, mean: np.ndarray, cov_inv: np.ndarray
) -> np.ndarray:
    """행별 Mahalanobis 거리(벡터화, 음수 클립). 학습 쪽 _mahalanobis 와 같은 식."""
    d = np.asarray(X, dtype=np.float64) - np.asarray(mean, dtype=np.float64)
    m2 = np.einsum("ij,jk,ik->i", d, np.asarray(cov_inv, dtype=np.float64), d)
    m2 = np.nan_to_num(m2, nan=0.0, posinf=0.0, neginf=0.0)
    return np.sqrt(np.clip(m2, 0.0, None))


@dataclass(frozen=True)
class AnomalyReport:
    """이상탐지 부가 결과(별도 채널 — 스키마 미변경).

    - loaded: 학습 모델 실제 사용 여부(False 면 classical 폴백).
    - distance: Mahalanobis 거리(모델 미로드 시 0).
    - threshold: 학습 임계(거리 단위).
    - score: 0~1 정규화 이상점수(거리/임계, 상한 1.0).
    - review_flag: 이상점수 임계 이상(=재확인 대상).
    """

    loaded: bool
    distance: float
    threshold: float
    score: float
    review_flag: bool


class AnomalySurfaceModel(SurfaceModel):
    """비지도 이상탐지 표면 모델(정상 분포 학습 → 이탈 탐지).

    학습(train_anomaly.py)한 품목별 npz(mean, cov_inv, threshold)를 로드한다.
    채점 방식(기술자 세트·격자·겹침·top-k)은 npz 에 적힌 대로 따른다 — 키가 없는
    구 모델은 v1·격자 1·겹침 없음·최악 패치로 예전과 똑같이 채점한다.
    모델이 없거나 로드 실패면 로드에러를 저장하고 고전 CV 폴백으로 동작한다.
    predict 는 classical 결과 SurfaceResult 를 반환하고, 이상탐지 부가정보는
    self.last_report 로 노출한다(파이프라인이 review_flag 에 반영).
    """

    def __init__(
        self,
        model_path: Optional[str] = None,
        *,
        item_code: Optional[str] = None,
    ) -> None:
        self.item_code = item_code
        self.model_path = resolve_anomaly_model_path(item_code, model_path)
        self._mean: Optional[np.ndarray] = None
        self._cov_inv: Optional[np.ndarray] = None
        self._threshold: Optional[float] = None
        self._feature_dim: Optional[int] = None
        #: 학습 시 사용한 패치 격자. npz 에 없으면 1(구 모델 = 전체 한 벡터).
        self._grid: int = 1
        #: 기술자 세트·겹침·이미지 점수 k. npz 에 없으면 구 모델(v1·겹침 없음·최악 패치).
        self._feature_set: str = FEATURE_SET_V1
        self._overlap: bool = False
        self._topk: int = 1
        self._load_error: Optional[str] = None
        self.last_report: Optional[AnomalyReport] = None
        if self.model_path:
            self._try_load(self.model_path)

    def _try_load(self, path: str) -> None:
        """npz 로드. 실패(손상/차원불일치)해도 폴백 가능하도록 삼킨다."""
        try:
            data = np.load(path, allow_pickle=False)
            mean = np.asarray(data["mean"], dtype=np.float64)
            cov_inv = np.asarray(data["cov_inv"], dtype=np.float64)
            thr = float(data["threshold"])
            fdim = int(data["feature_dim"])
            # 아래 키는 구 모델에는 없다. 없으면 예전 값으로 둬 기존 동작을 그대로
            # 유지한다(격자 1 · v1 기술자 · 겹침 없음 · 최악 패치).
            files = set(data.files)
            grid = int(data["patch_grid"]) if "patch_grid" in files else 1
            fset = (
                str(data["feature_set"]) if "feature_set" in files else FEATURE_SET_V1
            )
            overlap = (
                bool(int(data["patch_overlap"])) if "patch_overlap" in files else False
            )
            topk = int(data["score_topk"]) if "score_topk" in files else 1
        except Exception as exc:  # noqa: BLE001 - 로드 실패 시 폴백
            self._load_error = f"이상탐지 모델 로드 실패({path}): {exc}"
            return
        if fset not in FEATURE_SETS:
            self._load_error = f"이상탐지 모델 기술자 세트 미지원({path}): {fset!r}"
            return
        want = feature_dim(fset)
        if (
            fdim != want
            or mean.shape != (want,)
            or cov_inv.shape != (want, want)
        ):
            self._load_error = (
                f"이상탐지 모델 차원 불일치({path}): fdim={fdim}, "
                f"mean={mean.shape}, cov_inv={cov_inv.shape}"
            )
            return
        if (
            not np.isfinite(mean).all()
            or not np.isfinite(cov_inv).all()
            or not math.isfinite(thr)
            or thr <= 0.0
        ):
            self._load_error = f"이상탐지 모델 값 비정상({path})"
            return
        self._mean = mean
        self._cov_inv = cov_inv
        self._threshold = thr
        self._feature_dim = fdim
        self._grid = max(1, grid)
        self._feature_set = fset
        self._overlap = overlap
        self._topk = max(1, topk)

    @property
    def loaded(self) -> bool:
        """학습 모델이 실제 로드되었는지(아니면 고전 CV 폴백)."""
        return self._mean is not None

    def predict(
        self,
        surface_region_bgr: np.ndarray,
        item: ItemMaster,
        *,
        mask: Optional[np.ndarray] = None,
    ) -> SurfaceResult:
        t0 = time.perf_counter()
        # 항상 먼저 고전 CV(named 코드/verdict 보존, 미판정 0).
        base = analyze_surface(surface_region_bgr, item, mask=mask)

        if not self.loaded:
            self.last_report = AnomalyReport(
                loaded=False, distance=0.0, threshold=0.0, score=0.0,
                review_flag=False,
            )
            return base

        try:
            if self._feature_set == FEATURE_SET_V1:
                # 구 모델 경로 — 예전 코드 그대로(채점이 비트 단위로 같아야 한다).
                feats = patch_descriptors(surface_region_bgr, mask, grid=self._grid)
                # 최악 패치로 이미지를 대표한다. 평균을 쓰면 정상 패치가 결함을 희석해
                # 패치로 나눈 의미가 사라진다.
                dist = max(
                    mahalanobis_distance(
                        v, self._mean, self._cov_inv  # type: ignore[arg-type]
                    )
                    for v in feats
                )
            else:
                feats = patch_features(
                    surface_region_bgr, mask, feature_set=self._feature_set,
                    grid=self._grid, overlap=self._overlap,
                )
                dist = image_score(
                    mahalanobis_rows(
                        feats, self._mean, self._cov_inv  # type: ignore[arg-type]
                    ),
                    self._topk,
                )
            thr = float(self._threshold)  # type: ignore[arg-type]
            ratio = dist / thr if thr > 0.0 else 0.0
            score = float(min(1.0, max(0.0, ratio)))
            review = ratio >= 1.0
        except Exception:  # noqa: BLE001 - 이상탐지 실패는 classical 폴백.
            self.last_report = AnomalyReport(
                loaded=True, distance=0.0,
                threshold=float(self._threshold or 0.0), score=0.0,
                review_flag=False,
            )
            return base

        self.last_report = AnomalyReport(
            loaded=True, distance=round(dist, 6), threshold=round(thr, 6),
            score=round(score, 4), review_flag=review,
        )
        # 이상탐지 추가 처리시간 반영(계측 유지). 결정적.
        elapsed = int(round((time.perf_counter() - t0) * 1000))
        if elapsed > base.proc_time_ms:
            base = base.model_copy(update={"proc_time_ms": elapsed})
        return base


def resolve_surface_model(
    item: ItemMaster,
    *,
    mode: Optional[str] = None,
    model_mode: Optional[str] = None,
) -> SurfaceModel:
    """표면 모델 팩토리(§6.3 seam). 우선순위: ONNX > 이상탐지 npz > 고전 CV.

    model_mode: AIVIS_SURFACE_MODEL(env) — onnx|anomaly|classical|auto(기본).
    - onnx     : 항상 OnnxSurfaceModel(로드 실패 시 내부적으로 classical 폴백).
    - anomaly  : 항상 AnomalySurfaceModel(모델 없으면 classical 폴백).
    - classical: 항상 고전 CV(ClassicalSurfaceModel).
    - auto     : ONNX 모델(AIVIS_SURFACE_ONNX/기본 경로)이 존재하고 **로드
                 성공**하면 OnnxSurfaceModel → 아니면 기존 이상탐지 npz 로직
                 → 아니면 ClassicalSurfaceModel.

    mode: AIVIS_SURFACE_ANOMALY(env) — on|off|auto(기본 auto). 기존 동작
    100% 호환(auto 에서 ONNX 가 없을 때의 경로는 현행과 완전 동일).
    - off : 이상탐지 사용 안 함(고전 CV) — 현행과 동일.
    - on  : 항상 AnomalySurfaceModel(모델 없으면 내부적으로 classical 폴백).
    - auto: 학습 모델이 있으면 AnomalySurfaceModel, 없으면 ClassicalSurfaceModel
            → 모델이 없을 때 현행 동작과 100% 동일(회귀 없음).
    """
    from .model import OnnxSurfaceModel, resolve_model_path

    item_code = getattr(item, "item_code", None)
    model_mode = (
        model_mode or os.environ.get("AIVIS_SURFACE_MODEL", "auto")
    ).lower()
    if model_mode == "classical":
        return ClassicalSurfaceModel()
    if model_mode == "onnx":
        return OnnxSurfaceModel()
    if model_mode == "anomaly":
        path = resolve_anomaly_model_path(item_code)
        return AnomalySurfaceModel(model_path=path, item_code=item_code)

    # auto: ONNX 가 존재하고 로드 성공하면 최우선.
    if resolve_model_path() is not None:
        onnx_model = OnnxSurfaceModel()
        if onnx_model.loaded:
            return onnx_model
    # 이하 기존 이상탐지 npz 로직(AIVIS_SURFACE_ANOMALY 존중 — 회귀 없음).
    mode = (mode or os.environ.get("AIVIS_SURFACE_ANOMALY", "auto")).lower()
    if mode == "off":
        return ClassicalSurfaceModel()
    path = resolve_anomaly_model_path(item_code)
    if mode == "on":
        return AnomalySurfaceModel(model_path=path, item_code=item_code)
    # auto
    if path is not None:
        return AnomalySurfaceModel(model_path=path, item_code=item_code)
    return ClassicalSurfaceModel()


__all__ = [
    "FEATURE_NAMES",
    "FEATURE_DIM",
    "FEATURE_NAMES_V2",
    "FEATURE_DIM_V2",
    "FEATURE_SET_V1",
    "FEATURE_SET_V2",
    "FEATURE_SETS",
    "DEFAULT_FEATURE_SET",
    "DEFAULT_PATCH_GRID",
    "DEFAULT_PATCH_OVERLAP",
    "DEFAULT_SCORE_TOPK",
    "AnomalyReport",
    "AnomalySurfaceModel",
    "extract_descriptor",
    "feature_dim",
    "image_score",
    "mahalanobis_distance",
    "mahalanobis_rows",
    "patch_descriptors",
    "patch_features",
    "patch_features_v2",
    "patch_layout",
    "resolve_anomaly_model_path",
    "resolve_surface_model",
]
