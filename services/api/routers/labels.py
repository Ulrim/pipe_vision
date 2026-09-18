"""정답 라벨링 라우터 (부록 A.2/A.5, §5 M16, §1.2).

**왜 필요한가**: 표면 결함 모델(유분기/변색/스크래치)의 진척을 막고 있는 것은
카메라도 알고리즘도 아니라 **정답셋**이다. 현장에는 검사 이미지가 수십 GB 쌓여
있지만, "이 사진이 유분기인지 변색인지"를 사람이 적어 둔 기록이 없어 학습도
정확도 측정(§1.2 항목별 95%)도 할 수 없었다. 라벨링 CLI 는 있었지만 현장 품질
담당자가 터미널을 쓰지 않으므로 실제로는 아무도 라벨을 만들지 못했다.

**큐 순서가 이 API 의 핵심**: 라벨링은 사람 시간이 드는 일이라 순서가 곧
비용이다. 아무 사진이나 주지 않고

  1) 재확인 대상(review_flag) — 모델이 임계 근처에서 헷갈린 것
  2) 시스템이 NG 로 본 것 — 불량 클래스는 늘 부족하다
  3) 나머지(정상 다수)

순으로 낸다. 모델이 이미 확신하는 정상품 1,000장보다 헷갈린 10장이 정확도를
더 올린다. 같은 순위 안에서는 오래된 것부터 — 오래된 이미지가 보관정리
(core/retention)로 먼저 사라지기 때문이다.

권한: 조회는 operator+, 저장은 quality+ (부록 A.5 검수 주체 = 품질담당).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aivis_types import (
    DefectCode,
    LabelIn,
    LabelOut,
    LabelProgress,
    LabelQueueItem,
    Role,
)
from core.security import CurrentUser, require_min_role
from db.base import get_db
from db.models import Inspection, InspectionLabel

router = APIRouter(prefix="/labels", tags=["labels"])

#: 부록 A.2 "1차 동작 모델" 최소 수량. 화면이 무엇이 부족한지 보여주는 기준.
#: 정확한 합격선이 아니라 수집 가이드다(운영 중 오검·미검으로 계속 보강, M16).
CLASS_TARGETS: dict[str, int] = {
    "OK": 150,
    DefectCode.LEN.value: 50,
    DefectCode.OIL.value: 50,
    DefectCode.DIS.value: 50,
    DefectCode.SCR.value: 50,
}


def _to_out(row: InspectionLabel) -> LabelOut:
    return LabelOut(
        inspection_id=row.inspection_id,
        labels=list(row.labels or []),
        border=bool(row.border),
        length_mm_gt=(
            float(row.length_mm_gt) if row.length_mm_gt is not None else None
        ),
        note=row.note,
        labeled_by=row.labeled_by,
        labeled_at=row.labeled_at,
    )


@router.get("/queue", response_model=list[LabelQueueItem])
def label_queue(
    limit: int = Query(20, ge=1, le=200),
    item_code: str | None = Query(None, description="품목 필터"),
    include_labeled: bool = Query(False, description="라벨링된 것도 포함"),
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(require_min_role(Role.OPERATOR)),
):
    """라벨링 대기열 — 값이 큰 것부터 준다(위 모듈 설명의 우선순위).

    이미지가 없는 행은 내지 않는다. 보관정리로 파일이 사라진 오래된 검사는
    라벨을 붙여도 학습에 쓸 수 없어 검수자 시간만 버린다.
    """
    q = select(Inspection).where(Inspection.result_image_path.isnot(None))
    if not include_labeled:
        labeled = select(InspectionLabel.inspection_id)
        q = q.where(Inspection.id.notin_(labeled))
    if item_code:
        q = q.where(Inspection.item_code == item_code)

    # 우선순위: 재확인 대상 → NG → 나머지. DB 이식성을 위해 정렬은 파이썬에서.
    # (limit 의 몇 배만 읽어 정렬하면 되므로 전체 스캔은 하지 않는다.)
    rows = list(
        db.execute(q.order_by(Inspection.inspected_at.asc()).limit(limit * 20))
        .scalars()
        .all()
    )

    def _rank(r: Inspection) -> tuple[int, datetime]:
        if r.review_flag:
            rank = 0
        elif r.final_verdict == "NG":
            rank = 1
        else:
            rank = 2
        ts = r.inspected_at
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (rank, ts)

    rows.sort(key=_rank)
    return [
        LabelQueueItem(
            inspection_id=r.id,
            lot=r.lot,
            item_code=r.item_code,
            inspected_at=r.inspected_at,
            final_verdict=r.final_verdict,
            defect_codes=list(r.defect_codes or []),
            review_flag=bool(r.review_flag),
            meas_length_mm=(
                float(r.meas_length_mm) if r.meas_length_mm is not None else None
            ),
            has_result_image=bool(r.result_image_path),
            has_raw_image=bool(r.raw_image_path),
        )
        for r in rows[:limit]
    ]


@router.get("/progress", response_model=LabelProgress)
def label_progress(
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(require_min_role(Role.OPERATOR)),
):
    """클래스별 라벨링 진척 vs 부록 A.2 목표수량."""
    labels = list(db.execute(select(InspectionLabel)).scalars().all())
    counts: dict[str, int] = {k: 0 for k in CLASS_TARGETS}
    border = 0
    for row in labels:
        codes = list(row.labels or [])
        if not codes:
            counts["OK"] = counts.get("OK", 0) + 1
        for c in codes:
            counts[c] = counts.get(c, 0) + 1
        if row.border:
            border += 1

    total_with_image = (
        db.execute(
            select(func.count())
            .select_from(Inspection)
            .where(Inspection.result_image_path.isnot(None))
        ).scalar()
        or 0
    )
    return LabelProgress(
        labeled_total=len(labels),
        unlabeled_total=max(0, total_with_image - len(labels)),
        border_count=border,
        by_class={
            code: {"count": counts.get(code, 0), "target": target}
            for code, target in CLASS_TARGETS.items()
        },
    )


@router.get("/export")
def export_groundtruth(
    item_code: str | None = Query(None),
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(require_min_role(Role.QUALITY)),
):
    """정답셋 매니페스트(JSON) — 학습·정확도 측정에 그대로 투입.

    항목별 정확도(§1.2)를 재려면 정답과 시스템 판정을 같은 행에 놓아야 하므로
    둘 다 싣는다. 이미지 경로는 AIVIS_IMAGES_DIR 기준 상대경로다(부록 A.6).
    """
    q = (
        select(InspectionLabel, Inspection)
        .join(Inspection, Inspection.id == InspectionLabel.inspection_id)
    )
    if item_code:
        q = q.where(Inspection.item_code == item_code)
    pairs = list(db.execute(q).all())
    items = [
        {
            "inspection_id": lab.inspection_id,
            "item_code": insp.item_code,
            "lot": insp.lot,
            "inspected_at": insp.inspected_at.isoformat(),
            "raw_image_path": insp.raw_image_path,
            "result_image_path": insp.result_image_path,
            # 사람이 붙인 정답
            "labels": list(lab.labels or []),
            "is_ok": not (lab.labels or []),
            "border": bool(lab.border),
            "length_mm_gt": (
                float(lab.length_mm_gt) if lab.length_mm_gt is not None else None
            ),
            # 시스템 판정(정확도 대조용)
            "system_verdict": insp.final_verdict,
            "system_defect_codes": list(insp.defect_codes or []),
            "labeled_by": lab.labeled_by,
        }
        for lab, insp in pairs
    ]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "item_code": item_code,
        "count": len(items),
        "items": items,
    }


@router.get("/{inspection_id}", response_model=LabelOut)
def get_label(
    inspection_id: int,
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(require_min_role(Role.OPERATOR)),
):
    row = db.get(InspectionLabel, inspection_id)
    if not row:
        raise HTTPException(status_code=404, detail="라벨 없음")
    return _to_out(row)


@router.put("/{inspection_id}", response_model=LabelOut)
def put_label(
    inspection_id: int,
    body: LabelIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_min_role(Role.QUALITY)),
):
    """라벨 저장(덮어쓰기). 검수 주체는 품질담당(부록 A.5)."""
    if not db.get(Inspection, inspection_id):
        raise HTTPException(status_code=404, detail="검사결과 없음")
    row = db.get(InspectionLabel, inspection_id)
    if not row:
        row = InspectionLabel(inspection_id=inspection_id)
        db.add(row)
    row.labels = list(body.labels)
    row.border = bool(body.border)
    row.length_mm_gt = body.length_mm_gt
    row.note = body.note
    row.labeled_by = user.username
    row.labeled_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return _to_out(row)


@router.delete("/{inspection_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_label(
    inspection_id: int,
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(require_min_role(Role.QUALITY)),
):
    """라벨 취소(잘못 붙인 경우). 검사결과 자체는 건드리지 않는다."""
    row = db.get(InspectionLabel, inspection_id)
    if row:
        db.delete(row)
        db.commit()
