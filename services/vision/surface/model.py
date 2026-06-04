"""표면 모델 인터페이스 (M4, §6.3 점진 고도화 자리).

데이터 축적 후 PyTorch 학습 → ONNX 배포 시 이 인터페이스를 구현해 교체한다.
지금은 고전 CV 폴백(classical.analyze_surface)이 기본 경로이며, 본 모듈은
ONNX 추론 백엔드의 결선 지점만 정의한다(인터페이스 우선, 미존재 시 폴백).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

import numpy as np
from aivis_types import ItemMaster, SurfaceResult

from .classical import analyze_surface


class SurfaceModel(ABC):
    """표면 결함 추론 모델 인터페이스. 모든 추론은 결정적이어야 한다."""

    @abstractmethod
    def predict(
        self,
        surface_region_bgr: np.ndarray,
        item: ItemMaster,
        *,
        mask: Optional[np.ndarray] = None,
    ) -> SurfaceResult: ...


class ClassicalSurfaceModel(SurfaceModel):
    """고전 CV 폴백을 SurfaceModel 인터페이스로 감싼 기본 구현."""

    def predict(
        self,
        surface_region_bgr: np.ndarray,
        item: ItemMaster,
        *,
        mask: Optional[np.ndarray] = None,
    ) -> SurfaceResult:
        return analyze_surface(surface_region_bgr, item, mask=mask)


class OnnxSurfaceModel(SurfaceModel):
    """ONNX Runtime 기반 표면 모델 (P7+ 데이터 축적 후 배포).

    모델 파일(models/surface_*.onnx)이 없으면 고전 CV 폴백으로 위임한다
    ("동작하는 폴백 → 점진 고도화" 전략, §6.3).
    """

    def __init__(self, model_path: Optional[str] = None) -> None:
        self.model_path = model_path
        self._session = None
        if model_path and Path(model_path).exists():
            self._load(model_path)

    def _load(self, model_path: str) -> None:  # pragma: no cover - 모델 배포 후
        import onnxruntime as ort

        self._session = ort.InferenceSession(
            model_path, providers=["CPUExecutionProvider"]
        )

    def predict(
        self,
        surface_region_bgr: np.ndarray,
        item: ItemMaster,
        *,
        mask: Optional[np.ndarray] = None,
    ) -> SurfaceResult:
        if self._session is None:
            # 모델 미배포 → 결정적 고전 CV 폴백.
            return analyze_surface(surface_region_bgr, item, mask=mask)
        # pragma: no cover - 실제 ONNX 추론은 모델 배포 후 결선.
        raise NotImplementedError(
            "OnnxSurfaceModel 추론 결선은 학습/ONNX export 완료 후 구현(§6.3)."
        )
