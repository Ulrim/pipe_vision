"""생산실적 집계 (docs/MES.md §4).

다발 튜브는 스테이션 사이에서 낱개로 추적되지 않는다(같은 튜브가 길이 검사와 표면
검사에서 각각 한 행). 그래서 단계를 **더하지 않고** 단계별로 센다.

- 단계별: 검사 수, AI 불량, 최종 불량(재확인 반영), 불량유형별.
  최종 판정 = 재확인 결과가 있으면 그것, 없으면 AI 판정.
- 불량 합계 = 단계별 최종 불량의 합. 각 스테이션에서 걸러 낸 튜브는 다음 공정으로
  가지 않으므로 겹치지 않는다.
- 양품(실적) = 크레이트 개수 확인 합계가 있으면 그것(실제로 담긴 수), 없으면 마지막
  검사 단계(표면 → 길이 순)의 검사 수 − 최종 불량.
- 진척률 = 양품 ÷ 지시수량.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from mes_app.models import QualityRecord, WorkOrder

CRATE = "CRATE_COUNT"
#: 표시·양품 판단 순서(뒤에 있을수록 나중 공정).
STAGE_ORDER = ("CUT_LENGTH", "POST_WASH_SURFACE")
STAGE_LABEL = {"CUT_LENGTH": "길이 검사", "POST_WASH_SURFACE": "표면 검사",
               CRATE: "개수 확인", "UNKNOWN": "단계 미상"}


@dataclass
class StageStat:
    stage: str
    label: str
    inspected: int = 0
    ai_ng: int = 0
    final_ng: int = 0
    reviewed: int = 0
    by_code: dict[str, int] = field(default_factory=dict)

    @property
    def good(self) -> int:
        return self.inspected - self.final_ng


@dataclass
class Production:
    inspected_rows: int = 0
    stages: list[StageStat] = field(default_factory=list)
    defect_total: int = 0
    by_code: dict[str, int] = field(default_factory=dict)
    good_qty: int = 0
    good_source: str = "none"  # crate | stage:<STAGE> | none
    crate_count: int = 0
    crate_tubes: int = 0
    crate_mismatch: int = 0
    first_at: Optional[datetime] = None
    last_at: Optional[datetime] = None

    def as_dict(self) -> dict:
        d = asdict(self)
        for s, st in zip(d["stages"], self.stages):
            s["good"] = st.good
        return d


def effective_verdict(r: QualityRecord) -> str:
    return (r.manual_verdict or r.final_verdict or "").upper()


def summarize(records: Iterable[QualityRecord]) -> Production:
    p = Production()
    stages: dict[str, StageStat] = {}
    for r in records:
        p.inspected_rows += 1
        ts = r.inspected_at
        if ts is not None:
            p.first_at = ts if p.first_at is None or ts < p.first_at else p.first_at
            p.last_at = ts if p.last_at is None or ts > p.last_at else p.last_at
        if r.inspection_stage == CRATE:
            p.crate_count += 1
            p.crate_tubes += int(r.count_detected or 0)
            if (r.final_verdict or "").upper() == "NG":
                p.crate_mismatch += 1
            continue
        key = r.inspection_stage or "UNKNOWN"
        st = stages.setdefault(key, StageStat(stage=key, label=STAGE_LABEL.get(key, key)))
        st.inspected += 1
        if (r.final_verdict or "").upper() == "NG":
            st.ai_ng += 1
        if r.manual_verdict:
            st.reviewed += 1
        if effective_verdict(r) == "NG":
            st.final_ng += 1
            # 재확인으로 NG 가 된 것(AI 는 OK)은 유형이 없다 — '재확인 NG' 로 센다.
            codes = [c for c in (r.defect_codes or []) if c != "MULTI"] or ["MANUAL"]
            for c in codes:
                st.by_code[c] = st.by_code.get(c, 0) + 1
                p.by_code[c] = p.by_code.get(c, 0) + 1

    order = {s: i for i, s in enumerate(STAGE_ORDER)}
    p.stages = sorted(stages.values(), key=lambda s: (order.get(s.stage, 99), s.stage))
    p.defect_total = sum(s.final_ng for s in p.stages)
    if p.crate_count:
        p.good_qty, p.good_source = p.crate_tubes, "crate"
    else:
        known = [s for s in p.stages if s.stage in order]
        last = known[-1] if known else (p.stages[-1] if p.stages else None)
        if last is not None:
            p.good_qty, p.good_source = last.good, f"stage:{last.stage}"
    return p


def records_for(db: Session, wo: WorkOrder) -> list[QualityRecord]:
    """작업지시의 검사결과: 작업지시 번호가 같거나, 번호 없이 LOT 이 같은 것."""
    return list(db.execute(
        select(QualityRecord).where(or_(
            QualityRecord.work_order == wo.wo_no,
            and_(QualityRecord.lot == wo.lot,
                 or_(QualityRecord.work_order.is_(None), QualityRecord.work_order == "")),
        ))
    ).scalars())


def progress_pct(good: int, plan: int) -> Optional[float]:
    return round(good / plan * 100.0, 1) if plan > 0 else None


def unassigned_lots(db: Session) -> list[dict]:
    """작업지시와 이어지지 않은 검사결과(LOT 별). 실적이 사라지지 않게 따로 보인다."""
    wos = db.execute(select(WorkOrder.wo_no, WorkOrder.lot)).all()
    wo_nos = {w for w, _ in wos}
    lots = {lot for _, lot in wos}
    groups: dict[str, list[QualityRecord]] = {}
    for r in db.execute(select(QualityRecord)).scalars():
        if (r.work_order and r.work_order in wo_nos) or r.lot in lots:
            continue
        groups.setdefault(r.lot, []).append(r)
    out = []
    for lot, rs in sorted(groups.items(), key=lambda kv: max(r.inspected_at for r in kv[1]),
                          reverse=True):
        p = summarize(rs)
        out.append({
            "lot": lot,
            "work_order": next((r.work_order for r in rs if r.work_order), None),
            "item_code": next((r.item_code for r in rs if r.item_code), None),
            "production": p.as_dict(),
        })
    return out
