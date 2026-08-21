"""표면 모델 인터페이스 + ONNX 결선 (M4, §6.3 점진 고도화).

GitHub 공개 모델을 포함해 **임의의 ONNX 모델 + 사이드카 json(onnx_meta)** 을
꽂으면 실제 추론이 돈다. 지원 계약(kind)은 surface3(3점수 분류)와
anomaly(anomalib 계열 이상탐지) 두 가지이며, 그 외/로드 실패/실행 실패는
전부 고전 CV 폴백(classical.analyze_surface)으로 안전하게 내려간다
(자동검사율 100% — 미판정 0).
"""
from __future__ import annotations

import logging
import os
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import numpy as np
from aivis_types import DefectCode, ItemMaster, SurfaceResult, Verdict

from .classical import analyze_surface
from .onnx_meta import (
    SURFACE3_KEYS,
    OnnxMeta,
    OnnxMetaError,
    load_onnx_meta,
    sidecar_path,
)

log = logging.getLogger("aivis.vision.surface")


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


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


class OnnxSurfaceModel(SurfaceModel):
    """ONNX Runtime 기반 표면 모델 — GitHub 공개 모델 적용 경로 (§6.3).

    계약(docs/OPEN_MODELS.md, surface/onnx_meta.py):
    - 모델 `.onnx` 와 같은 basename 의 사이드카 `<모델>.json` 이 입출력
      계약(kind/전처리/출력 매핑)을 정의한다.
    - 사이드카가 없으면 출력 이름이 정확히 oil/discolor/scratch 인 모델만
      surface3 로 자동 인식(구 export 골격 관례). 그 외는 로드 실패 처리
      (_load_error 에 사이드카 안내) → classical 폴백. 조용한 오동작 금지.

    "동작하는 폴백 → 점진 고도화" 전략:
    - 모델 파일 없음/onnxruntime 미설치/로드·실행 실패 → **고전 CV 폴백**
      (자동검사율 100%, 미판정 0). predict 는 항상 결정적 SurfaceResult.
    - kind=surface3: 3점수(0~1) → **ItemMaster 임계**로 판정(classical 과
      동일 규칙 — 하드코딩 금지).
    - kind=anomaly : classical 점수/코드 보존 + 이상점수 ≥ threshold 면
      재확인(review) 채널(last_report — AnomalySurfaceModel 과 동일 패턴).
      사이드카 anomaly.force_ng=true 인 경우에만 MULTI + NG 강제.
    - providers: AIVIS_ONNX_PROVIDERS(쉼표구분) > CPU.
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
        self.meta: Optional[OnnxMeta] = None
        self._input_name: Optional[str] = None
        self._output_names: List[str] = []
        self._load_error: Optional[str] = None
        self._runtime_warned = False
        self.last_report = None  # AnomalyReport(kind=anomaly 시 갱신)
        if self.model_path:
            self._try_load(self.model_path)

    @staticmethod
    def _default_providers() -> list:
        env = os.environ.get("AIVIS_ONNX_PROVIDERS")
        if env:
            return [p.strip() for p in env.split(",") if p.strip()]
        return ["CPUExecutionProvider"]

    def _try_load(self, model_path: str) -> None:
        """ONNX 세션+사이드카 로드. 실패해도 폴백 가능하도록 삼킨다."""
        try:
            import onnxruntime as ort  # type: ignore
        except ImportError as exc:
            self._load_error = f"onnxruntime 미설치: {exc}"
            self._session = None
            return
        try:
            session = ort.InferenceSession(model_path, providers=self.providers)
        except Exception as exc:  # noqa: BLE001 - 로드 실패 시 폴백
            self._load_error = f"ONNX 로드 실패({model_path}): {exc}"
            self._session = None
            return

        meta = self._resolve_meta(session, model_path)
        if meta is None:  # _load_error 는 _resolve_meta 가 채운다.
            self._session = None
            return

        input_names = [i.name for i in session.get_inputs()]
        if not input_names:
            self._load_error = f"ONNX 입력이 없다({model_path})"
            self._session = None
            return
        if meta.input_name is not None and meta.input_name not in input_names:
            self._load_error = (
                f"사이드카 input_name={meta.input_name!r} 이 모델 입력"
                f"{input_names} 에 없다({model_path})"
            )
            self._session = None
            return

        self._session = session
        self.meta = meta
        self._input_name = meta.input_name or input_names[0]
        self._output_names = [o.name for o in session.get_outputs()]

    def _resolve_meta(self, session, model_path: str) -> Optional[OnnxMeta]:
        """사이드카 로드 또는(부재 시) 구 관례 자동 인식. 실패 시 None."""
        sc = sidecar_path(model_path)
        if sc.exists():
            try:
                return load_onnx_meta(sc)
            except OnnxMetaError as exc:
                self._load_error = str(exc)
                return None
        # 사이드카 없음: 출력 이름이 정확히 oil/discolor/scratch 인 모델만
        # surface3 로 자동 인식(구 export 골격 관례).
        out_names = [o.name for o in session.get_outputs()]
        if sorted(out_names) == sorted(SURFACE3_KEYS):
            size = self._static_input_hw(session)
            if size is None:
                self._load_error = (
                    f"입력 크기를 모델에서 정할 수 없다(동적 축) — "
                    f"사이드카 {sc} 필요"
                )
                return None
            return OnnxMeta(
                kind="surface3",
                input_size=size,
                outputs={k: k for k in SURFACE3_KEYS},
            )
        self._load_error = (
            f"출력 이름 {out_names} 은 지원 계약이 아니다 — "
            f"사이드카 {sc} 필요(docs/OPEN_MODELS.md 참고)"
        )
        return None

    @staticmethod
    def _static_input_hw(session) -> Optional[tuple]:
        """첫 입력의 정적 (H, W). NCHW/NHWC 추정, 동적 축이면 None."""
        shape = session.get_inputs()[0].shape
        if not isinstance(shape, (list, tuple)) or len(shape) != 4:
            return None
        if not all(isinstance(d, int) and d > 0 for d in shape):
            return None
        if shape[1] == 3:  # NCHW
            return int(shape[2]), int(shape[3])
        if shape[3] == 3:  # NHWC
            return int(shape[1]), int(shape[2])
        return None

    @property
    def loaded(self) -> bool:
        """ONNX 세션+계약이 실제 로드되었는지(아니면 고전 CV 폴백)."""
        return self._session is not None and self.meta is not None

    # --- 추론 ---
    def predict(
        self,
        surface_region_bgr: np.ndarray,
        item: ItemMaster,
        *,
        mask: Optional[np.ndarray] = None,
    ) -> SurfaceResult:
        if not self.loaded:
            # 모델 미배포/로드실패 → 결정적 고전 CV 폴백(미판정 0).
            return analyze_surface(surface_region_bgr, item, mask=mask)
        return self._infer(surface_region_bgr, item, mask=mask)

    def _infer(
        self,
        surface_region_bgr: np.ndarray,
        item: ItemMaster,
        *,
        mask: Optional[np.ndarray] = None,
    ) -> SurfaceResult:
        assert self.meta is not None
        if self.meta.kind == "anomaly":
            return self._infer_anomaly(surface_region_bgr, item, mask=mask)
        return self._infer_surface3(surface_region_bgr, item, mask=mask)

    def _preprocess(self, region_bgr: np.ndarray) -> np.ndarray:
        """BGR ROI → 모델 입력 텐서(float32, 배치 1). 결정적."""
        meta = self.meta
        assert meta is not None
        if region_bgr is None or region_bgr.ndim != 3 or region_bgr.shape[2] != 3:
            raise ValueError("surface_region 은 BGR 3채널이어야 한다")
        img = region_bgr
        if meta.color == "RGB":
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        h, w = meta.input_size
        img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
        x = img.astype(np.float32) * float(meta.scale)
        x = (x - np.asarray(meta.mean, dtype=np.float32)) / np.asarray(
            meta.std, dtype=np.float32
        )
        if meta.layout == "NCHW":
            x = np.transpose(x, (2, 0, 1))
        return np.ascontiguousarray(x[np.newaxis, ...], dtype=np.float32)

    def _run(self, region_bgr: np.ndarray) -> List[np.ndarray]:
        tensor = self._preprocess(region_bgr)
        assert self._session is not None and self._input_name is not None
        return self._session.run(None, {self._input_name: tensor})

    def _warn_runtime(self, exc: Exception) -> None:
        """실행 실패 경고는 최초 1회만 warning, 이후 debug(스팸 금지)."""
        msg = (
            f"ONNX 추론 실패({self.model_path}) → 고전 CV 폴백: "
            f"{type(exc).__name__}: {exc}"
        )
        if not self._runtime_warned:
            log.warning(msg)
            self._runtime_warned = True
        else:
            log.debug(msg)

    # --- kind=surface3 ---
    def _extract_surface3(self, outputs: List[np.ndarray]) -> Dict[str, float]:
        meta = self.meta
        assert meta is not None
        by_name: Dict[str, Any] = dict(zip(self._output_names, outputs))
        flat0 = np.asarray(outputs[0], dtype=np.float64).reshape(-1)
        scores: Dict[str, float] = {}
        for key in SURFACE3_KEYS:
            ref = meta.outputs[key]
            if isinstance(ref, str):
                if ref not in by_name:
                    raise KeyError(f"출력 이름 {ref!r} 없음: {self._output_names}")
                val = float(np.asarray(by_name[ref], dtype=np.float64).reshape(-1)[0])
            else:
                val = float(flat0[int(ref)])  # 범위 밖이면 IndexError → 폴백.
            if meta.apply_sigmoid:
                val = float(_sigmoid(np.float64(val)))
            scores[key] = float(min(1.0, max(0.0, val)))
        return scores

    def _infer_surface3(
        self,
        region_bgr: np.ndarray,
        item: ItemMaster,
        *,
        mask: Optional[np.ndarray] = None,
    ) -> SurfaceResult:
        t0 = time.perf_counter()
        try:
            outputs = self._run(region_bgr)
            scores = self._extract_surface3(outputs)
        except Exception as exc:  # noqa: BLE001 - 실행 실패는 classical 폴백.
            self._warn_runtime(exc)
            return analyze_surface(region_bgr, item, mask=mask)

        # 임계는 ItemMaster 에서(classical.analyze_surface 와 동일 규칙 —
        # None 이면 보수적 기본 0.5. 운영은 기준정보로 관리, 하드코딩 금지).
        oil_th = item.oil_threshold if item.oil_threshold is not None else 0.5
        dis_th = (
            item.discolor_threshold
            if item.discolor_threshold is not None
            else 0.5
        )
        scr_th = (
            item.scratch_threshold
            if item.scratch_threshold is not None
            else 0.5
        )
        codes: List[DefectCode] = []
        if scores["oil"] >= float(oil_th):
            codes.append(DefectCode.OIL)
        if scores["discolor"] >= float(dis_th):
            codes.append(DefectCode.DIS)
        if scores["scratch"] >= float(scr_th):
            codes.append(DefectCode.SCR)
        verdict = Verdict.NG if codes else Verdict.OK
        elapsed = int(round((time.perf_counter() - t0) * 1000))
        self.last_report = None  # surface3 는 review 부가채널을 쓰지 않는다.
        return SurfaceResult(
            oil_score=round(scores["oil"], 4),
            discolor_score=round(scores["discolor"], 4),
            scratch_score=round(scores["scratch"], 4),
            surface_verdict=verdict,
            defect_codes=codes,
            proc_time_ms=elapsed,
        )

    # --- kind=anomaly ---
    def _extract_anomaly_score(self, outputs: List[np.ndarray]) -> float:
        meta = self.meta
        assert meta is not None
        arr = np.asarray(outputs[0], dtype=np.float64)
        if arr.size == 0:
            raise ValueError("anomaly 출력이 비었다")
        raw = float(arr.reshape(-1)[0]) if arr.size == 1 else float(arr.max())
        if meta.apply_sigmoid:
            raw = float(_sigmoid(np.float64(raw)))
        return raw

    def _infer_anomaly(
        self,
        region_bgr: np.ndarray,
        item: ItemMaster,
        *,
        mask: Optional[np.ndarray] = None,
    ) -> SurfaceResult:
        from .anomaly import AnomalyReport  # 순환 import 방지(지연 import).

        meta = self.meta
        assert meta is not None
        t0 = time.perf_counter()
        # 항상 먼저 고전 CV — named 점수/코드/verdict 보존(미판정 0).
        base = analyze_surface(region_bgr, item, mask=mask)
        thr = float(meta.anomaly_threshold)
        try:
            outputs = self._run(region_bgr)
            raw = self._extract_anomaly_score(outputs)
        except Exception as exc:  # noqa: BLE001 - 실행 실패는 classical 폴백.
            self._warn_runtime(exc)
            self.last_report = AnomalyReport(
                loaded=True, distance=0.0, threshold=round(thr, 6),
                score=0.0, review_flag=False,
            )
            return base

        ratio = raw / thr if thr > 0.0 else 0.0
        score = float(min(1.0, max(0.0, ratio)))
        review = ratio >= 1.0
        self.last_report = AnomalyReport(
            loaded=True, distance=round(raw, 6), threshold=round(thr, 6),
            score=round(score, 4), review_flag=review,
        )
        # force_ng: 현장이 검증 후 명시적으로 켜는 옵션(사이드카)에서만 NG 강제.
        if review and meta.anomaly_force_ng:
            codes = list(base.defect_codes)
            if DefectCode.MULTI not in codes:
                codes.append(DefectCode.MULTI)
            base = base.model_copy(
                update={
                    "defect_codes": codes,
                    "surface_verdict": Verdict.NG.value,
                }
            )
        elapsed = int(round((time.perf_counter() - t0) * 1000))
        if elapsed > base.proc_time_ms:
            base = base.model_copy(update={"proc_time_ms": elapsed})
        return base
