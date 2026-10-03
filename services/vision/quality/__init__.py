"""품질/측정시스템 분석 유틸 (제품 코드).

MSA(길이 반복성/재현성, §5 M3 DoD)를 tests/ 밖 제품 코드로 둔다 — 현장
(라즈베리파이)에서 CLI(tools/run_msa.py)로 FAT/SAT 인수 자료를 산출할 수 있게
한다. tests/harness/msa.py 는 하위호환을 위해 이 모듈을 re-export 한다.

`msa` 와 `budget` 은 서로 다른 질문에 답한다 — 둘 다 필요하다.
  - `msa`: 찍힌 이미지로 **파이프라인**이 얼마나 흔들리는가(결정적이면 0).
  - `budget`: 그 광학 구성으로 **애초에** 그 공차를 잴 수 있는가(물리).
MSA 가 통과했는데 현장에서 안 맞는다면 거의 항상 budget 쪽이 범인이다.
"""
from __future__ import annotations

from .budget import (
    PI_CAMERAS,
    BudgetResult,
    CameraOptics,
    FrameFit,
    OpticalSetup,
    fit_frame,
    focal_for,
    length_budget,
    required_fov_mm,
    working_distance_mm,
)
from .msa import MsaResult, run_msa, write_msa_reports

__all__ = [
    "MsaResult",
    "run_msa",
    "write_msa_reports",
    "BudgetResult",
    "CameraOptics",
    "PI_CAMERAS",
    "working_distance_mm",
    "focal_for",
    "FrameFit",
    "OpticalSetup",
    "fit_frame",
    "length_budget",
    "required_fov_mm",
]
