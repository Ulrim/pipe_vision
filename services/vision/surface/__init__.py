"""표면 결함 판정 모듈 (M4) — CLAUDE.md §6.3.

데이터 부족 초기에는 고전 CV 폴백(휴리스틱)으로 동작 보장 →
데이터 축적 후 PyTorch 학습 → ONNX 배포로 점진 고도화.

- oil      : 하이라이트/얼룩 마스킹 휴리스틱 → oil_score.
- discolor : LAB 색공간 이상영역 → discolor_score.
- scratch  : 사광 가정 선형 에지/모폴로지 → scratch_score (+ 위치).
- ONNX 자리는 model.py 인터페이스만(SurfaceModel), 기본은 고전 CV 폴백.

임계값은 ItemMaster(oil/discolor/scratch_threshold)에서 읽는다(하드코딩 금지).
SurfaceResult(aivis_types) 를 반환하며 proc_time_ms 를 계측한다.
"""
from __future__ import annotations

from .classical import (
    ScratchLocation,
    SurfaceScores,
    analyze_surface,
    score_discolor,
    score_oil,
    score_scratch,
)
from .anomaly import (
    FEATURE_DIM,
    FEATURE_NAMES,
    AnomalyReport,
    AnomalySurfaceModel,
    extract_descriptor,
    mahalanobis_distance,
    resolve_anomaly_model_path,
    resolve_surface_model,
)
from .model import (
    ClassicalSurfaceModel,
    OnnxSurfaceModel,
    SurfaceModel,
    resolve_model_path,
)

__all__ = [
    "analyze_surface",
    "score_oil",
    "score_discolor",
    "score_scratch",
    "SurfaceScores",
    "ScratchLocation",
    "SurfaceModel",
    "ClassicalSurfaceModel",
    "OnnxSurfaceModel",
    "resolve_model_path",
    # 이상탐지(비지도, §6.3)
    "AnomalySurfaceModel",
    "AnomalyReport",
    "extract_descriptor",
    "mahalanobis_distance",
    "resolve_anomaly_model_path",
    "resolve_surface_model",
    "FEATURE_DIM",
    "FEATURE_NAMES",
]
