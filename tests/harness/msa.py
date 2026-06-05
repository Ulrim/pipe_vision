"""MSA — 길이 반복성/재현성(GR&R 근사), §5 M3 DoD.

동일 샘플을 30회(기본) 반복 측정해:
  - 반복성(Repeatability, EV): 동일 조건 반복 측정의 변동(σ_within).
  - 재현성(Reproducibility, AV): 측정자/조건(여기선 surface_inset_ratio 등
    파이프라인 변형)별 평균 변동(σ_between).
  - GR&R = sqrt(EV^2 + AV^2), %GR&R = 6σ_GRR / 공차 × 100.

파이프라인은 결정적이므로 동일 입력 반복 시 변동이 0 에 수렴함을 검증하는 것이
핵심(반복성 우수). 재현성은 약간의 입력 섭동(노이즈/조건) 하에서의 안정성을 본다.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Dict, List, Optional, Sequence

import numpy as np

from aivis_types import ItemMaster

from vision.pipeline import InspectionPipeline


def _std(values: Sequence[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    return math.sqrt(var)


@dataclass
class MsaResult:
    repeats: int
    samples: int
    appraisers: int
    measurements: List[List[float]]            # [appraiser][repeat] 의 측정 mm (단일 샘플)
    repeatability_std_mm: float                # EV σ
    reproducibility_std_mm: float              # AV σ
    grr_std_mm: float                          # sqrt(EV^2+AV^2)
    tolerance_mm: float
    pct_grr_tolerance: float                   # 6σ_GRR / 공차 × 100
    range_mm: float
    passed: bool

    def as_dict(self) -> dict:
        return {
            "repeats": self.repeats,
            "samples": self.samples,
            "appraisers": self.appraisers,
            "repeatability_std_mm": round(self.repeatability_std_mm, 6),
            "reproducibility_std_mm": round(self.reproducibility_std_mm, 6),
            "grr_std_mm": round(self.grr_std_mm, 6),
            "tolerance_mm": round(self.tolerance_mm, 4),
            "pct_grr_tolerance": round(self.pct_grr_tolerance, 4),
            "range_mm": round(self.range_mm, 6),
            "passed": self.passed,
        }


def run_msa(
    image,
    item: ItemMaster,
    *,
    repeats: int = 30,
    appraiser_insets: Optional[Sequence[float]] = None,
    grr_pct_max: float = 30.0,
) -> MsaResult:
    """동일 샘플 1개를 repeats 회 반복 측정 → 반복성/재현성/GR&R.

    appraiser_insets: 재현성(측정자/조건 변동) 모사용 surface_inset_ratio 목록.
      길이 측정 자체는 length_roi 기반이라 영향이 작아야 하며(안정성), 다중 조건
      평균 변동을 재현성으로 본다. 미지정 시 기본 파이프라인 단일 조건.
    grr_pct_max: %GR&R 합격 상한(공차 대비). 측정시스템 양호 기준(AIAG 관례 30%).
    """
    insets = list(appraiser_insets or [0.06])
    tol = float(item.tol_plus_mm) + float(item.tol_minus_mm)

    # [appraiser][repeat] 측정값.
    measurements: List[List[float]] = []
    for inset in insets:
        pipe = InspectionPipeline(surface_inset_ratio=inset)
        pipe.run(image, item)  # 워밍업
        row: List[float] = []
        for _ in range(repeats):
            v = pipe.run(image.copy(), item)
            m = v.length.meas_length_mm
            row.append(float(m) if m is not None else float("nan"))
        measurements.append(row)

    # 반복성(EV): 각 appraiser 내 반복 표준편차의 RMS.
    within_stds = [_std(row) for row in measurements]
    ev = math.sqrt(sum(s * s for s in within_stds) / len(within_stds)) if within_stds else 0.0

    # 재현성(AV): appraiser 평균들 간 표준편차.
    appraiser_means = [sum(r) / len(r) for r in measurements] if measurements else []
    av = _std(appraiser_means)

    grr = math.sqrt(ev * ev + av * av)
    pct_grr = (6.0 * grr / tol * 100.0) if tol > 0 else 0.0

    all_vals = [v for row in measurements for v in row]
    rng = (max(all_vals) - min(all_vals)) if all_vals else 0.0

    return MsaResult(
        repeats=repeats,
        samples=1,
        appraisers=len(insets),
        measurements=measurements,
        repeatability_std_mm=ev,
        reproducibility_std_mm=av,
        grr_std_mm=grr,
        tolerance_mm=tol,
        pct_grr_tolerance=pct_grr,
        range_mm=rng,
        passed=pct_grr <= grr_pct_max,
    )
