"""정답셋(라벨 포함 합성 데이터셋) 생성 — 부록 A.4/A.5 규격.

services/vision/tools/gen_synthetic.make_image 로 클래스별 결정적 합성 이미지를
만들고, 부록 A.4 파일명 + 사이드카 .json(라벨/length_mm_gt/scale)을 함께 기록한다.
data-ops 의 groundtruth.build_groundtruth 가 이 폴더를 그대로 읽어 정답셋을 구성한다.

gen_synthetic.write_dataset 은 사이드카를 남기지 않으므로(폴백=파일명) 여기서
사이드카를 직접 기록해 length_mm_gt 등 풍부한 정답을 제공한다.

라벨 매핑(부록 A.5, §7.2):
  - OK            -> labels=[]
  - LEN_PLUS/MINUS-> labels=["LEN"]   (파일 클래스 토큰 LEN)
  - OIL/DIS/SCR   -> labels=[그 코드]
  - MULTI         -> labels=["OIL","DIS","SCR","MULTI"]  (합성이 3종 동시 주입)

길이 GT: make_image 의 유효 파이프 길이(px) × px_to_mm_scale.
  OK/표면결함 = 500px, LEN_PLUS=580px, LEN_MINUS=420px (gen_synthetic 상수와 정합).
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Dict, List, Sequence

import cv2

from vision.tools.gen_synthetic import (
    DEFAULT_PIPE_LEN_PX,
    make_image,
)

# ItemMaster 기준값 (services/vision/tests/conftest.py 와 동일 — 단일 진실원 정합).
SCALE = 0.25
REF_MM = round(DEFAULT_PIPE_LEN_PX * SCALE, 3)  # 125.0
LEN_DELTA_PX = 80  # gen_synthetic: LEN_PLUS=+80px, LEN_MINUS=-80px

# 합성 클래스 -> (파일 클래스 토큰, 라벨 배열, 유효 파이프 길이 px)
_CLASS_SPEC: Dict[str, tuple] = {
    "OK":        ("OK",  [],                          DEFAULT_PIPE_LEN_PX),
    "LEN_PLUS":  ("LEN", ["LEN"],                     DEFAULT_PIPE_LEN_PX + LEN_DELTA_PX),
    "LEN_MINUS": ("LEN", ["LEN"],                     DEFAULT_PIPE_LEN_PX - LEN_DELTA_PX),
    "OIL":       ("OIL", ["OIL"],                     DEFAULT_PIPE_LEN_PX),
    "DIS":       ("DIS", ["DIS"],                     DEFAULT_PIPE_LEN_PX),
    "SCR":       ("SCR", ["SCR"],                     DEFAULT_PIPE_LEN_PX),
    "MULTI":     ("MULTI", ["OIL", "DIS", "SCR", "MULTI"], DEFAULT_PIPE_LEN_PX),
}

# 항목별 정확도(지표2) 평가 대상 항목 (부록 A.5 / §1.2).
DEFECT_ITEMS = ["LEN", "OIL", "DIS", "SCR"]


@dataclass
class SampleSpec:
    """생성된 1건의 명세(정답 포함)."""

    path: Path
    synth_class: str          # OK/LEN_PLUS/.../MULTI
    file_class: str           # 파일 클래스 토큰(OK/LEN/OIL/DIS/SCR/MULTI)
    labels: List[str]         # 불량 코드 배열(정상=[])
    length_mm_gt: float
    seed: int


def length_gt_mm(synth_class: str) -> float:
    """합성 클래스의 길이 정답(mm). px*scale, 소수 셋째 자리 반올림."""
    _ftoken, _labels, eff_len = _CLASS_SPEC[synth_class]
    return round(eff_len * SCALE, 3)


def default_class_mix(per_class: int) -> Dict[str, int]:
    """클래스별 균형 생성 카운트. per_class 를 각 합성 클래스에 동일 배정."""
    return {c: per_class for c in _CLASS_SPEC}


def write_groundtruth_dataset(
    out_dir: str | Path,
    *,
    class_counts: Dict[str, int] | None = None,
    per_class: int = 30,
    item_code: str = "HP12",
    view: str = "SIDE",
) -> List[SampleSpec]:
    """부록 A.4 폴더/파일명 + 사이드카 .json 으로 정답셋 데이터셋 생성.

    out_dir/<FILE_CLASS>/{item}_{view}_{FILE_CLASS}_{ts}_{seq}.jpg  (+ 동일명 .json)
    결정적: seed 와 타임스탬프 고정.
    """
    counts = class_counts or default_class_mix(per_class)
    out = Path(out_dir)
    specs: List[SampleSpec] = []

    for synth_class, n in counts.items():
        ftoken, labels, _eff = _CLASS_SPEC[synth_class]
        sub = out / ftoken
        sub.mkdir(parents=True, exist_ok=True)
        lgt = length_gt_mm(synth_class)
        for i in range(n):
            img, _bbox = make_image(synth_class, seed=i)
            ts = f"20260101-{(i // 60) % 24:02d}{i % 60:02d}00"
            seq = f"{i:04d}"
            stem = f"{item_code}_{view}_{ftoken}_{ts}_{seq}"
            img_path = sub / f"{stem}.jpg"
            cv2.imwrite(str(img_path), img)

            sidecar = {
                "item_code": item_code,
                "view": view,
                "labels": list(labels),
                "border": False,
                "length_mm_gt": lgt,
                "scale_ref_mm": 100.0,
                "lighting": "raking" if "SCR" in labels else "diffuse",
                "inspector": "qa-synth",
                "captured_at": "2026-01-01T00:00:00+09:00",
                "note": f"synthetic {synth_class} seed={i}",
            }
            with open(sub / f"{stem}.json", "w", encoding="utf-8") as f:
                json.dump(sidecar, f, ensure_ascii=False, indent=2)

            specs.append(
                SampleSpec(
                    path=img_path,
                    synth_class=synth_class,
                    file_class=ftoken,
                    labels=list(labels),
                    length_mm_gt=lgt,
                    seed=i,
                )
            )
    return specs
