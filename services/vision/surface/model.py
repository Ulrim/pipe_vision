"""표면 모델 인터페이스 (M4, §6.3 점진 고도화 자리).

데이터 축적 후 PyTorch 학습 → ONNX 배포 시 이 인터페이스를 구현해 교체한다.
지금은 고전 CV 폴백(classical.analyze_surface)이 기본 경로이며, 본 모듈은
ONNX 추론 백엔드의 결선 지점만 정의한다(인터페이스 우선, 미존재 시 폴백).
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

import numpy as np
from aivis_types import ItemMaster, SurfaceResult

from .classical import analyze_surface


def resolve_model_path(model_path: Optional[str] = None) -> Optional[str]:
    """표면 ONNX 모델 경로 결정(하드코딩 금지 — env/인자에서).

    우선순위: 명시 인자 > AIVIS_SURFACE_ONNX(env) > services/vision/models/
    아래 기본 파일명(surface.onnx). 존재하지 않으면 None(→ 고전 CV 폴백).
    """
    cand = model_path or os.environ.get("AIVIS_SURFACE_ONNX")
    if cand:
        return cand if Path(cand).exists() else None
    default = Path(__file__).resolve().parents[1] / "models" / "surface.onnx"
    return str(default) if default.exists() else None


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

    "동작하는 폴백 → 점진 고도화" 전략(§6.3):
    - 모델 파일이 없거나 onnxruntime 미설치/로드 실패면 **고전 CV 폴백**으로
      위임한다(자동검사율 100% 유지 — 미판정 0). 즉 어떤 환경에서도 predict 가
      결정적 SurfaceResult 를 반환한다.
    - 모델 경로는 env(AIVIS_SURFACE_ONNX)/인자에서 읽고, 임계는 ItemMaster
      에서 읽는다(하드코딩 금지).
    - providers: AIVIS_ONNX_PROVIDERS(쉼표구분) > CPU. GPU 가용 시
      'CUDAExecutionProvider,CPUExecutionProvider'.

    통합 단계 작업 목록(TODO, 학습/ONNX export 완료 후):
    1. 전처리: surface_region_bgr → 모델 입력(리사이즈/정규화/CHW). 결정적.
    2. session.run() → 클래스 확률/세그 마스크.
    3. 후처리: oil/discolor/scratch_score(0~1) 산출 + ItemMaster 임계로 verdict.
    4. _to_surface_result()로 SurfaceResult(+proc_time_ms) 구성.
    """

    def __init__(
        self,
        model_path: Optional[str] = None,
        *,
        providers: Optional[list] = None,
    ) -> None:
        self.model_path = resolve_model_path(model_path)
        self.providers = providers or self._default_providers()
        self._session = None
        self._load_error: Optional[str] = None
        if self.model_path:
            self._try_load(self.model_path)

    @staticmethod
    def _default_providers() -> list:
        env = os.environ.get("AIVIS_ONNX_PROVIDERS")
        if env:
            return [p.strip() for p in env.split(",") if p.strip()]
        return ["CPUExecutionProvider"]

    def _try_load(self, model_path: str) -> None:
        """ONNX 세션 로드. 실패(미설치/손상)해도 폴백 가능하도록 삼킨다."""
        try:
            import onnxruntime as ort  # type: ignore
        except ImportError as exc:
            self._load_error = f"onnxruntime 미설치: {exc}"
            self._session = None
            return
        try:  # pragma: no cover - 실제 모델 배포 후
            self._session = ort.InferenceSession(
                model_path, providers=self.providers
            )
        except Exception as exc:  # noqa: BLE001 - 로드 실패 시 폴백
            self._load_error = f"ONNX 로드 실패({model_path}): {exc}"
            self._session = None

    @property
    def loaded(self) -> bool:
        """ONNX 세션이 실제로 로드되었는지(아니면 고전 CV 폴백)."""
        return self._session is not None

    def predict(
        self,
        surface_region_bgr: np.ndarray,
        item: ItemMaster,
        *,
        mask: Optional[np.ndarray] = None,
    ) -> SurfaceResult:
        if self._session is None:
            # 모델 미배포/로드실패 → 결정적 고전 CV 폴백(미판정 0).
            return analyze_surface(surface_region_bgr, item, mask=mask)
        # pragma: no cover - 실제 ONNX 추론은 모델 배포 후 결선.
        return self._infer(surface_region_bgr, item, mask=mask)

    def _infer(  # pragma: no cover - 모델 배포 후 결선
        self,
        surface_region_bgr: np.ndarray,
        item: ItemMaster,
        *,
        mask: Optional[np.ndarray] = None,
    ) -> SurfaceResult:
        # TODO(P7): 전처리→session.run→후처리(ItemMaster 임계)→SurfaceResult.
        # 결선 전까지는 안전하게 고전 CV 폴백으로 위임(미판정 0 보장).
        return analyze_surface(surface_region_bgr, item, mask=mask)
