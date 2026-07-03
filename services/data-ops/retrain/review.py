"""오검·미검 추출 + 재학습 데이터셋 빌드 (CLAUDE.md §5 M16, §6.4).

오검(false positive)·미검(false negative) 정의:
- 작업자 재확인 결과(manual_verdict)가 시스템 최종 판정(final_verdict)과 다르면
  오검/미검 후보. review_flag=true 로 분리 태깅된 행도 후보.
- 오검: 시스템 NG → 사람 OK (system_ng_human_ok)
- 미검: 시스템 OK → 사람 NG (system_ok_human_ng)

§6.4 review/ 버킷 규격으로 이미지를 모아 재학습 데이터셋 매니페스트(JSON)를 낸다.
실제 파일 복사는 옵션(copy=True). DB는 backend 모델을 import 만 한다(변경 금지).
"""
from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from db.models import Inspection


def _miss_kind(final_verdict: str | None, manual_verdict: str | None) -> str | None:
    """오검/미검 유형 분류. 일치하거나 manual 미입력이면 None."""
    if manual_verdict is None or final_verdict is None:
        return None
    if manual_verdict == final_verdict:
        return None
    if final_verdict == "NG" and manual_verdict == "OK":
        return "system_ng_human_ok"   # 오검(과검출)
    if final_verdict == "OK" and manual_verdict == "NG":
        return "system_ok_human_ng"   # 미검(누락)
    return "mismatch"


@dataclass
class ReviewCandidate:
    """재학습 후보 1건."""

    inspection_id: int
    lot: str
    item_code: str | None
    final_verdict: str | None
    manual_verdict: str | None
    miss_kind: str | None          # 오검/미검 유형
    review_flag: bool
    defect_codes: list[str] = field(default_factory=list)
    raw_image_path: str | None = None
    result_image_path: str | None = None
    oil_score: float | None = None
    discolor_score: float | None = None
    scratch_score: float | None = None
    deviation_mm: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _to_candidate(row: Inspection) -> ReviewCandidate:
    return ReviewCandidate(
        inspection_id=row.id,
        lot=row.lot,
        item_code=row.item_code,
        final_verdict=row.final_verdict,
        manual_verdict=row.manual_verdict,
        miss_kind=_miss_kind(row.final_verdict, row.manual_verdict),
        review_flag=bool(row.review_flag),
        defect_codes=list(row.defect_codes or []),
        raw_image_path=row.raw_image_path,
        result_image_path=row.result_image_path,
        oil_score=float(row.oil_score) if row.oil_score is not None else None,
        discolor_score=float(row.discolor_score) if row.discolor_score is not None else None,
        scratch_score=float(row.scratch_score) if row.scratch_score is not None else None,
        deviation_mm=float(row.deviation_mm) if row.deviation_mm is not None else None,
    )


def extract_review_candidates(
    db: Session,
    *,
    item_code: str | None = None,
    include_flagged: bool = True,
    only_mismatch: bool = True,
) -> list[ReviewCandidate]:
    """재학습 후보 추출.

    - only_mismatch=True: manual_verdict != final_verdict 인 행만(핵심 오검/미검).
    - include_flagged=True: review_flag=true 행도 포함(재확인 대상으로 분류된 것).
    """
    conds = []
    mismatch_cond = (
        Inspection.manual_verdict.is_not(None)
        & (Inspection.manual_verdict != Inspection.final_verdict)
    )
    if only_mismatch and not include_flagged:
        conds.append(mismatch_cond)
    elif include_flagged and not only_mismatch:
        conds.append(Inspection.review_flag.is_(True))
    else:
        conds.append(or_(mismatch_cond, Inspection.review_flag.is_(True)))

    stmt = select(Inspection).where(*conds)
    if item_code:
        stmt = stmt.where(Inspection.item_code == item_code)
    stmt = stmt.order_by(Inspection.inspected_at.asc())

    rows = db.execute(stmt).scalars().all()
    return [_to_candidate(r) for r in rows]


def build_retrain_manifest(
    db: Session,
    out_dir: str,
    *,
    item_code: str | None = None,
    copy: bool = False,
    image_root: str | None = None,
) -> dict[str, Any]:
    """재학습 데이터셋 매니페스트(JSON)를 out_dir 에 생성. §6.4 review 버킷 규격.

    copy=True 면 raw/result 이미지를 out_dir/review/{miss_kind}/ 로 복사한다
    (image_root 기준 상대경로 해석). copy=False 면 경로만 기록(원본 불변 보존).
    """
    candidates = extract_review_candidates(db, item_code=item_code)
    os.makedirs(out_dir, exist_ok=True)

    by_kind: dict[str, int] = {}
    for c in candidates:
        key = c.miss_kind or "flagged"
        by_kind[key] = by_kind.get(key, 0) + 1
        if copy and c.raw_image_path:
            src = c.raw_image_path
            if image_root and not os.path.isabs(src):
                src = os.path.join(image_root, src)
            if os.path.exists(src):
                dst_dir = os.path.join(out_dir, "review", key)
                os.makedirs(dst_dir, exist_ok=True)
                shutil.copy2(src, os.path.join(dst_dir, os.path.basename(src)))

    manifest = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "item_code": item_code,
        "total": len(candidates),
        "by_kind": by_kind,
        "false_positive": by_kind.get("system_ng_human_ok", 0),  # 오검
        "false_negative": by_kind.get("system_ok_human_ng", 0),  # 미검
        "items": [c.as_dict() for c in candidates],
    }
    manifest_path = os.path.join(out_dir, "retrain_manifest.json")
    import json

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    manifest["manifest_path"] = manifest_path
    return manifest
