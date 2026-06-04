"""임계값 보정 워크플로우 (CLAUDE.md §5 M16, §6.3 항목별 임계).

현재 임계(item_master.{oil,discolor,scratch}_threshold) 대비, 사람 재확인 결과
(manual_verdict)를 정답으로 본 점수 분포에서 제안 임계를 산출해 비교한다.

방법: 표면 항목별로 사람이 NG 라 한 표본의 점수와 OK 라 한 표본의 점수를 모아,
두 분포를 가장 잘 가르는 임계(후보 임계 중 정확도 최대)를 제안한다. 데이터가
부족하면 현재 임계를 유지(보수적). 결과는 사람이 검토 후 적용하는 "제안"이다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Inspection, ItemMaster

# (표면 항목 -> inspection 점수 컬럼, item_master 임계 컬럼)
_SURFACE_FIELDS = {
    "oil": ("oil_score", "oil_threshold"),
    "discolor": ("discolor_score", "discolor_threshold"),
    "scratch": ("scratch_score", "scratch_threshold"),
}


@dataclass
class ThresholdSuggestion:
    """단일 표면 항목 임계 제안."""

    feature: str                  # oil | discolor | scratch
    current: float | None         # 현재 임계
    suggested: float | None       # 제안 임계(None=데이터 부족, 유지)
    samples: int                  # 사용 표본 수
    accuracy_current: float | None
    accuracy_suggested: float | None
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _accuracy(scores_labels: list[tuple[float, bool]], thr: float) -> float:
    """thr 로 분류 시 정확도. label True=NG(결함 있음). score>=thr 면 NG 예측."""
    if not scores_labels:
        return 0.0
    correct = sum(1 for s, ng in scores_labels if (s >= thr) == ng)
    return correct / len(scores_labels)


def _best_threshold(scores_labels: list[tuple[float, bool]]) -> tuple[float, float]:
    """후보 임계(표본 점수들의 중간값) 중 정확도 최대인 임계와 정확도 반환."""
    scores = sorted({s for s, _ in scores_labels})
    if not scores:
        return 0.5, 0.0
    # 후보: 인접 점수의 중점 + 양끝.
    cands: list[float] = []
    cands.append(max(0.0, scores[0] - 1e-6))
    for a, b in zip(scores, scores[1:]):
        cands.append(round((a + b) / 2.0, 6))
    cands.append(min(1.0, scores[-1] + 1e-6))

    best_thr, best_acc = cands[0], -1.0
    for thr in cands:
        acc = _accuracy(scores_labels, thr)
        if acc > best_acc:
            best_thr, best_acc = thr, acc
    return best_thr, best_acc


def suggest_thresholds(
    db: Session,
    item_code: str,
    *,
    min_samples: int = 10,
) -> list[ThresholdSuggestion]:
    """item_code 의 표면 항목별 임계 제안.

    정답: manual_verdict 가 입력된 행만 사용(사람이 검수한 것). NG=결함 존재로 간주.
    각 항목 점수가 있는 행을 모아 (점수, NG여부)로 임계를 탐색한다.
    """
    item = db.get(ItemMaster, item_code)
    rows = db.execute(
        select(Inspection).where(
            Inspection.item_code == item_code,
            Inspection.manual_verdict.is_not(None),
        )
    ).scalars().all()

    suggestions: list[ThresholdSuggestion] = []
    for feature, (score_col, thr_col) in _SURFACE_FIELDS.items():
        current = getattr(item, thr_col, None) if item else None
        current_f = float(current) if current is not None else None

        data: list[tuple[float, bool]] = []
        for r in rows:
            score = getattr(r, score_col)
            if score is None:
                continue
            ng = (r.manual_verdict == "NG")
            data.append((float(score), ng))

        if len(data) < min_samples:
            suggestions.append(ThresholdSuggestion(
                feature=feature, current=current_f, suggested=None,
                samples=len(data), accuracy_current=None, accuracy_suggested=None,
                note=f"표본 부족(<{min_samples}) → 현재 임계 유지",
            ))
            continue

        best_thr, best_acc = _best_threshold(data)
        acc_cur = _accuracy(data, current_f) if current_f is not None else None
        suggestions.append(ThresholdSuggestion(
            feature=feature,
            current=current_f,
            suggested=round(best_thr, 4),
            samples=len(data),
            accuracy_current=round(acc_cur, 4) if acc_cur is not None else None,
            accuracy_suggested=round(best_acc, 4),
            note="사람 재확인(manual_verdict) 기준 정확도 최대 임계 제안",
        ))
    return suggestions
