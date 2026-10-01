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


def mahalanobis_distance(
    x: np.ndarray, mean: np.ndarray, cov_inv: np.ndarray
) -> float:
    """Mahalanobis 거리 sqrt((x-μ)ᵀ Σ⁻¹ (x-μ)). 결정적, 음수 클립."""
    d = (x.astype(np.float64) - mean.astype(np.float64))
    m2 = float(d @ cov_inv @ d)
    if not math.isfinite(m2) or m2 < 0.0:
        m2 = max(0.0, m2) if math.isfinite(m2) else 0.0
    return math.sqrt(m2)


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
            # 구 모델에는 없는 키다. 없으면 1 로 둬 기존 동작을 그대로 유지한다.
            grid = int(data["patch_grid"]) if "patch_grid" in data.files else 1
        except Exception as exc:  # noqa: BLE001 - 로드 실패 시 폴백
            self._load_error = f"이상탐지 모델 로드 실패({path}): {exc}"
            return
        if (
            fdim != FEATURE_DIM
            or mean.shape != (FEATURE_DIM,)
            or cov_inv.shape != (FEATURE_DIM, FEATURE_DIM)
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
            feats = patch_descriptors(surface_region_bgr, mask, grid=self._grid)
            # 최악 패치로 이미지를 대표한다. 평균을 쓰면 정상 패치가 결함을 희석해
            # 패치로 나눈 의미가 사라진다.
            dist = max(
                mahalanobis_distance(
                    v, self._mean, self._cov_inv  # type: ignore[arg-type]
                )
                for v in feats
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
    "AnomalyReport",
    "AnomalySurfaceModel",
    "extract_descriptor",
    "mahalanobis_distance",
    "resolve_anomaly_model_path",
    "resolve_surface_model",
]
