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

    fg = _foreground_mask(region_bgr, mask)
    if int(fg.sum()) == 0:
        return np.zeros(FEATURE_DIM, dtype=np.float64)

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
    L, A, B = lab[:, :, 0], lab[:, :, 1], lab[:, :, 2]
    Lf, Af, Bf = L[fg], A[fg], B[fg]

    # 그래디언트 크기(텍스처).
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    gmag = np.sqrt(gx * gx + gy * gy)
    gmag_f = gmag[fg_er]

    # 국소 대비(라플라시안 분산).
    lap = cv2.Laplacian(gray, cv2.CV_32F, ksize=3)
    lap_f = lap[fg_er]

    # 하이라이트(유분/반사): 포화 비율 + top-hat 얼룩 비율.
    sat_ratio = float(np.mean(gray[fg] >= 245))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
    tophat = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kernel)
    tophat_ratio = float(np.mean(tophat[fg] >= 40))

    # 에지 밀도(스크래치/구조).
    g0 = gray.copy()
    g0[~fg] = 0
    edges = cv2.Canny(g0, 60, 160)
    edge_density = float(np.mean(edges[fg_er] > 0))

    # colorfulness(Hasler-Susstrunk) — 변색 민감.
    Bc = region_bgr[:, :, 0].astype(np.float32)
    Gc = region_bgr[:, :, 1].astype(np.float32)
    Rc = region_bgr[:, :, 2].astype(np.float32)
    rg = (Rc - Gc)[fg]
    yb = (0.5 * (Rc + Gc) - Bc)[fg]
    colorfulness = float(
        math.sqrt(float(rg.std()) ** 2 + float(yb.std()) ** 2)
        + 0.3 * math.sqrt(float(rg.mean()) ** 2 + float(yb.mean()) ** 2)
    )

    # 변색: a/b 중앙값 대비 색이탈 거리 통계.
    a_med, b_med = float(np.median(Af)), float(np.median(Bf))
    ab_dist = np.sqrt((Af - a_med) ** 2 + (Bf - b_med) ** 2)
    ab_dist_mean = float(ab_dist.mean())
    ab_dist_p95 = float(np.percentile(ab_dist, 95))

    gray_f = gray[fg].astype(np.float32)
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
            ab_dist_mean,
            ab_dist_p95,
            gp10,
            gp50,
            gp90,
        ],
        dtype=np.float64,
    )
    # 수치 안정: 비정상값 방어.
    return np.nan_to_num(vec, nan=0.0, posinf=0.0, neginf=0.0)


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
            vec = extract_descriptor(surface_region_bgr, mask)
            dist = mahalanobis_distance(
                vec, self._mean, self._cov_inv  # type: ignore[arg-type]
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
    item: ItemMaster, *, mode: Optional[str] = None
) -> SurfaceModel:
    """표면 모델 팩토리(§6.3 seam). 품목에 이상탐지 모델이 있으면 사용.

    mode: AIVIS_SURFACE_ANOMALY(env) — on|off|auto(기본 auto).
    - off : 항상 고전 CV(ClassicalSurfaceModel) — 현행과 동일.
    - on  : 항상 AnomalySurfaceModel(모델 없으면 내부적으로 classical 폴백).
    - auto: 학습 모델이 있으면 AnomalySurfaceModel, 없으면 ClassicalSurfaceModel
            → 모델이 없을 때 현행 동작과 100% 동일(회귀 없음).
    """
    item_code = getattr(item, "item_code", None)
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
