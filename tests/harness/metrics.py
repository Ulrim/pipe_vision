"""지표 산출 — §1.2 4개 인수지표 + 혼동행렬 + 백분위.

지표1 자동검사율, 지표2 항목별 정확도/혼동행렬, 지표3 처리속도 백분위,
지표4 저장·연계율 산출을 한 곳에서 정의한다(FAT/SAT 공유).
"""
from __future__ import annotations

import os

from dataclasses import dataclass, field
import math
from typing import Dict, Optional, Sequence

from aivis_types import ItemMaster

# §1.2 인수 합격 임계.
AUTO_RATE_MIN = 100.0          # 자동검사율 100%
ITEM_ACCURACY_MIN = 95.0       # 항목별 정확도 ≥95%
PROC_P95_MAX_MS = 300.0        # 처리속도 ≤300ms (p95 기준 차단)
STORAGE_MES_RATE_MIN = 100.0   # 저장·연계율 100%

DEFECT_ITEMS = ["LEN", "OIL", "DIS", "SCR"]


def make_item_master(item_code: str = "HP12") -> ItemMaster:
    """정답셋 합성 기하와 정합하는 기준정보(§7.1).

    services/vision/tests/conftest.py 의 픽스처와 동일 값(단일 진실원 정합):
    ref 125mm, 공차 ±3mm, scale 0.25, oil 0.30 / dis 0.20 / scr 0.15.

    **이 값은 합성 캔버스 전용이다.** 실사진에 쓰면 안 된다 —
    item_master_for_real_dataset() 을 쓴다.
    """
    return ItemMaster(
        item_code=item_code,
        item_name="Header Pipe 12",
        ref_length_mm=125.0,
        tol_plus_mm=3.0,
        tol_minus_mm=3.0,
        px_to_mm_scale=0.25,
        oil_threshold=0.30,
        discolor_threshold=0.20,
        scratch_threshold=0.15,
    )


class CalibrationMissing(RuntimeError):
    """실데이터를 돌리려는데 현장 캘리브레이션 값이 없다."""


def item_master_for_real_dataset(item_code: str = "HP12") -> ItemMaster:
    """실사진용 기준정보. 현장 캘리브레이션 값을 **환경변수로 반드시 받는다**.

    왜 기본값을 두지 않는가: 합성 기준정보(125mm, 0.25mm/px)는 800x300 도형
    캔버스에 맞춰진 숫자다. 이걸로 실사진을 재면 측정 길이가 전혀 다른 값이 나와
    길이 판정(LEN)이 통째로 무의미해진다. 그런데 리포트에는 "real dataset" 으로
    찍히므로, 합성으로 돌린 것보다 **더 위험하다** — 검증된 것처럼 보이기 때문이다.
    그래서 값이 없으면 조용히 기본값으로 떨어지지 않고 멈춘다.

    px_to_mm_scale 은 스케일 기준자를 함께 찍은 캘리브레이션 촬영(부록 A.3)에서
    산출한다. 추정값을 넣으면 안 된다.
    """
    missing = [
        name
        for name in ("AIVIS_ITEM_REF_LENGTH_MM", "AIVIS_ITEM_PX_TO_MM_SCALE")
        if not os.getenv(name)
    ]
    if missing:
        raise CalibrationMissing(
            "실데이터로 검증하려면 현장 캘리브레이션 값이 필요합니다. "
            f"누락: {', '.join(missing)}\n"
            "  AIVIS_ITEM_REF_LENGTH_MM=250.0      # 품목 기준 길이(mm)\n"
            "  AIVIS_ITEM_PX_TO_MM_SCALE=0.130208  # 캘리브레이션으로 산출한 px→mm\n"
            "  (선택) AIVIS_ITEM_TOL_PLUS_MM / _TOL_MINUS_MM / "
            "AIVIS_ITEM_OIL_TH / _DIS_TH / _SCR_TH\n"
            "합성 기준값(125mm, 0.25mm/px)은 도형 캔버스 전용이라 실사진에 쓰면 "
            "길이 판정이 무의미해집니다."
        )

    def _f(name: str, default: float) -> float:
        raw = os.getenv(name)
        if raw is None or raw.strip() == "":
            return default
        try:
            return float(raw)
        except ValueError as exc:
            raise CalibrationMissing(f"{name} 값이 숫자가 아닙니다: {raw!r}") from exc

    return ItemMaster(
        item_code=os.getenv("AIVIS_ITEM_CODE", item_code),
        item_name=os.getenv("AIVIS_ITEM_NAME", "Header Pipe"),
        ref_length_mm=_f("AIVIS_ITEM_REF_LENGTH_MM", 0.0),
        tol_plus_mm=_f("AIVIS_ITEM_TOL_PLUS_MM", 0.5),
        tol_minus_mm=_f("AIVIS_ITEM_TOL_MINUS_MM", 0.5),
        px_to_mm_scale=_f("AIVIS_ITEM_PX_TO_MM_SCALE", 0.0),
        oil_threshold=_f("AIVIS_ITEM_OIL_TH", 0.5),
        discolor_threshold=_f("AIVIS_ITEM_DIS_TH", 0.5),
        scratch_threshold=_f("AIVIS_ITEM_SCR_TH", 0.5),
    )


def percentile(values: Sequence[float], pct: float) -> float:
    """선형보간 백분위(numpy 의존 없이). values 비어있으면 0.0."""
    if not values:
        return 0.0
    xs = sorted(values)
    if len(xs) == 1:
        return float(xs[0])
    k = (len(xs) - 1) * (pct / 100.0)
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return float(xs[int(k)])
    return float(xs[lo] + (xs[hi] - xs[lo]) * (k - lo))


@dataclass
class LatencyReport:
    """처리속도 백분위 리포트(지표3). 1,000장 배치 p50/p95/p99/max."""

    count: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float
    mean_ms: float
    over_300_count: int
    passed: bool
    #: 측정 구간. §1.2 정의는 "이미지 취득~결과 저장" — acquire_to_save.
    #: (2026-10-10 점검 전에는 판정 구간만 재서 짧게 나왔다.)
    scope: str = "acquire_to_save"
    #: 단계별 평균 ms {read_ms, infer_ms, save_ms}.
    breakdown: dict = field(default_factory=dict)
    #: 참고: 판정(파이프라인)만의 백분위 — 종전 지표와 비교용. 판정에 쓰지 않는다.
    infer_only: Optional[dict] = None

    def as_dict(self) -> dict:
        return {
            "scope": self.scope,
            "breakdown_mean_ms": {k: round(v, 3) for k, v in self.breakdown.items()},
            "infer_only": self.infer_only,
            "count": self.count,
            "p50_ms": round(self.p50_ms, 3),
            "p95_ms": round(self.p95_ms, 3),
            "p99_ms": round(self.p99_ms, 3),
            "max_ms": round(self.max_ms, 3),
            "mean_ms": round(self.mean_ms, 3),
            "over_300_count": self.over_300_count,
            "threshold_ms": PROC_P95_MAX_MS,
            "passed": self.passed,
        }


def latency_report(proc_times_ms: Sequence[float]) -> LatencyReport:
    p95 = percentile(proc_times_ms, 95)
    return LatencyReport(
        count=len(proc_times_ms),
        p50_ms=percentile(proc_times_ms, 50),
        p95_ms=p95,
        p99_ms=percentile(proc_times_ms, 99),
        max_ms=max(proc_times_ms) if proc_times_ms else 0.0,
        mean_ms=(sum(proc_times_ms) / len(proc_times_ms)) if proc_times_ms else 0.0,
        over_300_count=sum(1 for t in proc_times_ms if t > PROC_P95_MAX_MS),
        passed=p95 <= PROC_P95_MAX_MS,
    )


@dataclass
class BinaryConfusion:
    """이진 혼동행렬(항목 존재=positive). 항목별 정확도(지표2)용."""

    item: str
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    @property
    def total(self) -> int:
        return self.tp + self.fp + self.fn + self.tn

    @property
    def accuracy_pct(self) -> float:
        return ((self.tp + self.tn) / self.total * 100.0) if self.total else 0.0

    @property
    def precision(self) -> float:
        d = self.tp + self.fp
        return (self.tp / d) if d else 1.0

    @property
    def recall(self) -> float:
        d = self.tp + self.fn
        return (self.tp / d) if d else 1.0

    def as_dict(self) -> dict:
        return {
            "item": self.item,
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "tn": self.tn,
            "total": self.total,
            "accuracy_pct": round(self.accuracy_pct, 3),
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "confusion_matrix": {
                "actual_pos": {"pred_pos": self.tp, "pred_neg": self.fn},
                "actual_neg": {"pred_pos": self.fp, "pred_neg": self.tn},
            },
        }


@dataclass
class ItemAccuracyReport:
    """항목별 정확도 종합(지표2). 항목별 혼동행렬 + 전체 최저 정확도."""

    per_item: Dict[str, BinaryConfusion] = field(default_factory=dict)

    def update(self, item: str, gt_pos: bool, pred_pos: bool) -> None:
        cm = self.per_item.setdefault(item, BinaryConfusion(item=item))
        if gt_pos and pred_pos:
            cm.tp += 1
        elif gt_pos and not pred_pos:
            cm.fn += 1
        elif (not gt_pos) and pred_pos:
            cm.fp += 1
        else:
            cm.tn += 1

    @property
    def min_accuracy_pct(self) -> float:
        if not self.per_item:
            return 0.0
        return min(cm.accuracy_pct for cm in self.per_item.values())

    @property
    def passed(self) -> bool:
        return self.min_accuracy_pct >= ITEM_ACCURACY_MIN

    def as_dict(self) -> dict:
        return {
            "threshold_pct": ITEM_ACCURACY_MIN,
            "min_accuracy_pct": round(self.min_accuracy_pct, 3),
            "passed": self.passed,
            "per_item": {k: v.as_dict() for k, v in self.per_item.items()},
        }


def gt_pred_to_item_flags(labels: Sequence[str]) -> Dict[str, bool]:
    """라벨 배열 -> 항목별 존재 플래그. MULTI 는 개별 코드로 이미 펼쳐져 있음."""
    s = set(labels)
    return {it: (it in s) for it in DEFECT_ITEMS}
