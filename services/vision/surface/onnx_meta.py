"""ONNX 표면 모델 사이드카 메타데이터 계약 (§6.3 — GitHub 공개 모델 적용 경로).

GitHub 공개 모델은 출력 형식이 제각각이므로, 모델 `.onnx` 와 같은 basename 의
사이드카 `<모델>.json` 으로 입출력 계약을 명시한다. 지원 kind 는 두 가지다.

- ``surface3``: 유분기/변색/스크래치 3점수(0~1) 출력 분류기.
  점수 → ItemMaster 임계 판정(classical 과 동일 규칙, 하드코딩 금지).
- ``anomaly`` : anomalib 계열 이상탐지(점수 스칼라 또는 anomaly map).
  classical 점수/코드를 보존하고 이상점수는 재확인(review) 채널로만 쓴다.

사이드카가 없으면 출력 이름이 정확히 oil/discolor/scratch 인 모델(구 export
골격 관례)만 surface3 로 자동 인식하고, 그 외에는 로드 실패로 처리해 classical
폴백한다(조용한 오동작 금지).

로더는 결정적이며, 모르는 kind/잘못된 필드는 명확한 한국어 오류를 낸다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Tuple, Union

#: 픽셀 스케일 기본값(uint8 → 0~1). torch 계열 관례.
DEFAULT_SCALE = 1.0 / 255.0
SUPPORTED_KINDS = ("surface3", "anomaly")
SURFACE3_KEYS = ("oil", "discolor", "scratch")
_LAYOUTS = ("NCHW", "NHWC")
_COLORS = ("RGB", "BGR")


class OnnxMetaError(ValueError):
    """사이드카 메타데이터 검증 오류(누락/모르는 kind/형식 오류)."""


@dataclass(frozen=True)
class OnnxMeta:
    """ONNX 모델 입출력 계약(사이드카 json 1:1 매핑, 기본값 처리 완료).

    - kind        : "surface3" | "anomaly".
    - input_size  : (H, W) 모델 입력 크기.
    - layout      : "NCHW"(기본) | "NHWC".
    - color       : "RGB"(기본, torch 관례) | "BGR".
    - scale       : 픽셀 스케일(기본 1/255).
    - mean/std    : 정규화 평균/표준편차(스케일 후). 기본 (0,0,0)/(1,1,1).
    - input_name  : None 이면 세션 첫 입력 사용.
    - apply_sigmoid: 출력이 logit 이면 True.
    - outputs     : kind=surface3 출력 매핑. 값이 int 면 첫 출력 텐서
                    평탄화 인덱스, str 이면 ONNX 출력 이름(스칼라).
    - anomaly_threshold/force_ng: kind=anomaly 판정 임계(이상점수)와
      NG 강제 여부(현장 검증 후 명시적으로 켜는 옵션).
    """

    kind: str
    input_size: Tuple[int, int]
    layout: str = "NCHW"
    color: str = "RGB"
    scale: float = DEFAULT_SCALE
    mean: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    std: Tuple[float, float, float] = (1.0, 1.0, 1.0)
    input_name: Optional[str] = None
    apply_sigmoid: bool = False
    outputs: Dict[str, Union[int, str]] = field(
        default_factory=lambda: {"oil": 0, "discolor": 1, "scratch": 2}
    )
    anomaly_threshold: float = 0.5
    anomaly_force_ng: bool = False


def sidecar_path(model_path: Union[str, Path]) -> Path:
    """모델 .onnx 와 같은 basename 의 사이드카 json 경로."""
    return Path(model_path).with_suffix(".json")


def _as_hw(value: object, *, ctx: str) -> Tuple[int, int]:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 2
        or not all(isinstance(v, int) and v > 0 for v in value)
    ):
        raise OnnxMetaError(
            f"{ctx}: input_size 는 [H, W] 양의 정수 2개여야 한다: {value!r}"
        )
    return int(value[0]), int(value[1])


def _as_triplet(
    value: object, default: Tuple[float, float, float], *, name: str, ctx: str
) -> Tuple[float, float, float]:
    if value is None:
        return default
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 3
        or not all(isinstance(v, (int, float)) for v in value)
    ):
        raise OnnxMetaError(f"{ctx}: {name} 은 실수 3개 배열이어야 한다: {value!r}")
    return float(value[0]), float(value[1]), float(value[2])


def _validate_outputs(value: object, *, ctx: str) -> Dict[str, Union[int, str]]:
    if value is None:
        return {"oil": 0, "discolor": 1, "scratch": 2}
    if not isinstance(value, dict) or set(value.keys()) != set(SURFACE3_KEYS):
        raise OnnxMetaError(
            f"{ctx}: outputs 는 {{oil, discolor, scratch}} 매핑이어야 한다: "
            f"{value!r}"
        )
    out: Dict[str, Union[int, str]] = {}
    for k in SURFACE3_KEYS:
        v = value[k]
        if isinstance(v, bool) or not isinstance(v, (int, str)):
            raise OnnxMetaError(
                f"{ctx}: outputs.{k} 는 정수 인덱스 또는 출력 이름(str)이어야 "
                f"한다: {v!r}"
            )
        if isinstance(v, int) and v < 0:
            raise OnnxMetaError(f"{ctx}: outputs.{k} 인덱스는 0 이상: {v}")
        out[k] = v
    return out


def load_onnx_meta(path: Union[str, Path]) -> OnnxMeta:
    """사이드카 json → OnnxMeta. 검증 실패 시 OnnxMetaError(한국어 사유).

    결정적: 같은 파일 → 같은 OnnxMeta. 기본값은 dataclass 정의를 따른다.
    """
    p = Path(path)
    ctx = f"사이드카({p})"
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise OnnxMetaError(f"{ctx}: 파일이 없다") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise OnnxMetaError(f"{ctx}: json 파싱 실패 — {exc}") from exc
    if not isinstance(raw, dict):
        raise OnnxMetaError(f"{ctx}: 최상위는 객체(dict)여야 한다")

    kind = raw.get("kind")
    if kind not in SUPPORTED_KINDS:
        raise OnnxMetaError(
            f"{ctx}: 모르는 kind={kind!r} — 지원: {list(SUPPORTED_KINDS)}"
        )
    if "input_size" not in raw:
        raise OnnxMetaError(f"{ctx}: 필수 필드 input_size([H,W]) 누락")
    input_size = _as_hw(raw["input_size"], ctx=ctx)

    layout = raw.get("layout", "NCHW")
    if layout not in _LAYOUTS:
        raise OnnxMetaError(f"{ctx}: layout 은 {_LAYOUTS} 중 하나: {layout!r}")
    color = raw.get("color", "RGB")
    if color not in _COLORS:
        raise OnnxMetaError(f"{ctx}: color 는 {_COLORS} 중 하나: {color!r}")

    scale_raw = raw.get("scale", DEFAULT_SCALE)
    if not isinstance(scale_raw, (int, float)) or float(scale_raw) <= 0.0:
        raise OnnxMetaError(f"{ctx}: scale 은 양의 실수여야 한다: {scale_raw!r}")
    mean = _as_triplet(raw.get("mean"), (0.0, 0.0, 0.0), name="mean", ctx=ctx)
    std = _as_triplet(raw.get("std"), (1.0, 1.0, 1.0), name="std", ctx=ctx)
    if any(v == 0.0 for v in std):
        raise OnnxMetaError(f"{ctx}: std 에 0 이 있으면 안 된다: {std!r}")

    input_name = raw.get("input_name")
    if input_name is not None and not isinstance(input_name, str):
        raise OnnxMetaError(f"{ctx}: input_name 은 문자열/null: {input_name!r}")
    apply_sigmoid = raw.get("apply_sigmoid", False)
    if not isinstance(apply_sigmoid, bool):
        raise OnnxMetaError(f"{ctx}: apply_sigmoid 는 bool: {apply_sigmoid!r}")

    outputs = {"oil": 0, "discolor": 1, "scratch": 2}
    if kind == "surface3":
        outputs = _validate_outputs(raw.get("outputs"), ctx=ctx)

    anomaly_threshold = 0.5
    anomaly_force_ng = False
    if kind == "anomaly":
        anom = raw.get("anomaly") or {}
        if not isinstance(anom, dict):
            raise OnnxMetaError(f"{ctx}: anomaly 는 객체여야 한다: {anom!r}")
        thr = anom.get("threshold", 0.5)
        if not isinstance(thr, (int, float)) or float(thr) <= 0.0:
            raise OnnxMetaError(
                f"{ctx}: anomaly.threshold 는 양의 실수여야 한다: {thr!r}"
            )
        force = anom.get("force_ng", False)
        if not isinstance(force, bool):
            raise OnnxMetaError(f"{ctx}: anomaly.force_ng 는 bool: {force!r}")
        anomaly_threshold = float(thr)
        anomaly_force_ng = force

    return OnnxMeta(
        kind=str(kind),
        input_size=input_size,
        layout=str(layout),
        color=str(color),
        scale=float(scale_raw),
        mean=mean,
        std=std,
        input_name=input_name,
        apply_sigmoid=apply_sigmoid,
        outputs=outputs,
        anomaly_threshold=anomaly_threshold,
        anomaly_force_ng=anomaly_force_ng,
    )


__all__ = [
    "DEFAULT_SCALE",
    "SUPPORTED_KINDS",
    "SURFACE3_KEYS",
    "OnnxMeta",
    "OnnxMetaError",
    "load_onnx_meta",
    "sidecar_path",
]
