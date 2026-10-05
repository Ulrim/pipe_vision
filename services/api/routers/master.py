"""기준정보 관리 라우터 (CLAUDE.md §5 M13, §7.4 CRUD /master/items).

변경 시 version 자동 증가, 수정 권한은 품질관리자/관리자(quality+)로 제한.
추가: /master/active — "현재 검사 오더"(발주 기반 품목/LOT/작업지시) 단일 행.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from aivis_types import (
    CalibrationRequest,
    InspectionStage,
    ItemMaster as ItemMasterSchema,
    ItemMasterCreate,
    ItemMasterUpdate,
    ItemSpecUpdate,
    LogCategory,
    Role,
)

from core.logging import write_log
from core.security import CurrentUser, require_min_role
from db.base import get_db
from db.models import ActiveOrder, ItemMaster
from db.serialize import item_to_schema

router = APIRouter(prefix="/master/items", tags=["master"])

#: 치수 사양(기준길이·공차)을 고칠 수 있는 최소 권한.
#:
#: **작업자(operator) 로 확정**(2026-10-03 도입기업 결정). 제품 길이가 주문마다
#: 바뀌는 공정이라 오더 교체 때마다 품질관리자를 부르면 라인이 선다. 권한을
#: 여는 대신 **바꿀 수 있는 항목을 기준길이·공차·개수로 좁히고**
#: (ItemSpecUpdate), 변경은 version 증가와 감사 로그(이전값→새값)로 남긴다.
#: px→mm 보정계수와 표면 임계값은 이 경로로 전송 자체가 불가능하다.
#:
#: AIVIS_SPEC_EDIT_MIN_ROLE 은 확정 정책을 뒤집는 스위치가 아니라, 현장 정책이
#: 바뀌었을 때 재배포 없이 조일 수 있는 비상구다(quality|admin). 기본값을 코드에서
#: 바꾸지 마라 — test_item_spec.py 가 operator 기본값을 못 박아 두었다.
_SPEC_EDIT_MIN_ROLE = {
    "operator": Role.OPERATOR,
    "quality": Role.QUALITY,
    "admin": Role.ADMIN,
}.get(os.getenv("AIVIS_SPEC_EDIT_MIN_ROLE", "operator").strip().lower(), Role.OPERATOR)

# /master/active 는 /master/items CRUD 와 prefix 가 달라 별도 라우터로 둔다
# (같은 라우터에 넣으면 /master/items/active 가 되어 GET /master/items/{item_code}
# 와 경로가 섞인다). main.py 에서 함께 등록한다.
active_router = APIRouter(prefix="/master", tags=["master"])


class ActiveOrderIn(BaseModel):
    """PUT /master/active 본문. 발주 1건 = 품목 + LOT(+작업지시)."""

    item_code: str
    lot: Optional[str] = None
    work_order: Optional[str] = None
    #: 검사 단계(=모드) 덮어쓰기. None 이면 워커 env 기본값(스테이션 고정).
    inspection_stage: Optional[InspectionStage] = None


class ActiveStageIn(BaseModel):
    """PUT /master/active/stage 본문 — **모드만** 바꾼다.

    오더 전체(PUT /master/active)는 quality+ 인데, 모드 전환은 작업자가 라인에서
    한다(길이 스테이션에서 개수 확인으로 카메라를 돌려 세우는 식). 그래서
    바꿀 수 있는 것을 모드 하나로 좁히고 권한을 열었다 — /spec 과 같은 설계.
    """

    item_code: str
    inspection_stage: InspectionStage


class ActiveOrderOut(ActiveOrderIn):
    """현재 검사 오더 응답. 미설정 시 엔드포인트가 JSON null 을 반환한다."""

    updated_by: Optional[str] = None
    updated_at: Optional[datetime] = None


def _active_out(row: ActiveOrder) -> ActiveOrderOut:
    return ActiveOrderOut(
        item_code=row.item_code,
        lot=row.lot,
        work_order=row.work_order,
        inspection_stage=row.inspection_stage,
        updated_by=row.updated_by,
        updated_at=row.updated_at,
    )


@active_router.get("/active", response_model=Optional[ActiveOrderOut])
def get_active_order(
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(require_min_role(Role.OPERATOR)),
):
    """현재 검사 오더 조회. 미설정이면 200 + JSON null (404 아님 — 분기 단순화).

    발주마다 품목(모양/외경/개수)·절단 길이가 달라지므로, 워커가 이 값을
    폴링(핫리로드 주기, 기본 15s)해 재시작 없이 품목/LOT/작업지시를 전환한다.
    미설정이면 워커는 기존 env(AIVIS_ITEM_CODE/AIVIS_LOT) 동작을 유지한다.
    """
    row = db.get(ActiveOrder, 1)
    return _active_out(row) if row else None


@active_router.put("/active", response_model=ActiveOrderOut)
def put_active_order(
    body: ActiveOrderIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_min_role(Role.QUALITY)),
):
    """현재 검사 오더 설정(단일 행 upsert, quality+).

    품목이 기준정보에 없으면 404. 저장 후 워커가 15초 내 자동 전환한다.
    """
    if not db.get(ItemMaster, body.item_code):
        raise HTTPException(status_code=404, detail="품목 없음(기준정보 먼저 등록)")
    row = db.get(ActiveOrder, 1)
    if not row:
        row = ActiveOrder(id=1)
        db.add(row)
    row.item_code = body.item_code
    row.lot = body.lot
    row.work_order = body.work_order
    stage = body.inspection_stage
    row.inspection_stage = stage.value if stage is not None else None
    row.updated_by = user.username
    row.updated_at = datetime.now(timezone.utc)
    write_log(
        db,
        category=LogCategory.USER,
        message=(
            f"master.active {body.item_code} lot={body.lot} "
            f"wo={body.work_order} stage={row.inspection_stage} by={user.username}"
        ),
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return _active_out(row)


@active_router.put("/active/stage", response_model=ActiveOrderOut)
def put_active_stage(
    body: ActiveStageIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_min_role(_SPEC_EDIT_MIN_ROLE)),
):
    """검사 모드(단계)만 바꾼다. 작업자 권한(= /spec 과 같은 정책).

    활성 오더가 없으면 품목으로 새로 만든다(LOT/작업지시는 비움) — 모드를
    바꾸려는데 "오더부터 설정하라" 고 막으면 벤치에서 아무것도 못 한다.
    품목이 기준정보에 없으면 404. 워커가 15초 내 자동 전환한다.
    """
    if not db.get(ItemMaster, body.item_code):
        raise HTTPException(status_code=404, detail="품목 없음(기준정보 먼저 등록)")
    row = db.get(ActiveOrder, 1)
    if not row:
        row = ActiveOrder(id=1, item_code=body.item_code)
        db.add(row)
    before = row.inspection_stage
    row.item_code = body.item_code
    row.inspection_stage = body.inspection_stage.value
    row.updated_by = user.username
    row.updated_at = datetime.now(timezone.utc)
    write_log(
        db,
        category=LogCategory.USER,
        message=(
            f"master.active.stage {body.item_code} {before}→{row.inspection_stage} "
            f"by={user.username}"
        ),
        payload={"before": before, "after": row.inspection_stage},
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return _active_out(row)


@active_router.delete("/active", status_code=status.HTTP_204_NO_CONTENT)
def clear_active_order(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_min_role(Role.QUALITY)),
):
    """현재 검사 오더 해제(quality+). 이후 워커는 env 기본 품목으로 복귀."""
    row = db.get(ActiveOrder, 1)
    if row:
        db.delete(row)
        write_log(
            db,
            category=LogCategory.USER,
            message=f"master.active clear by={user.username}",
            commit=False,
        )
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("", response_model=list[ItemMasterSchema])
def list_items(
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(require_min_role(Role.OPERATOR)),
):
    """기준정보 목록 조회. 로그인 필요(operator+)."""
    rows = db.execute(select(ItemMaster).order_by(ItemMaster.item_code)).scalars().all()
    return [item_to_schema(r) for r in rows]


@router.get("/{item_code}", response_model=ItemMasterSchema)
def get_item(
    item_code: str,
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(require_min_role(Role.OPERATOR)),
):
    """기준정보 단건 조회. 로그인 필요(operator+)."""
    row = db.get(ItemMaster, item_code)
    if not row:
        raise HTTPException(status_code=404, detail="품목 없음")
    return item_to_schema(row)


@router.post("", response_model=ItemMasterSchema, status_code=status.HTTP_201_CREATED)
def create_item(
    body: ItemMasterCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_min_role(Role.QUALITY)),
):
    if db.get(ItemMaster, body.item_code):
        raise HTTPException(status_code=409, detail="이미 존재하는 품목")
    row = ItemMaster(
        item_code=body.item_code,
        item_name=body.item_name,
        ref_length_mm=body.ref_length_mm,
        tol_plus_mm=body.tol_plus_mm,
        tol_minus_mm=body.tol_minus_mm,
        px_to_mm_scale=body.px_to_mm_scale,
        oil_threshold=body.oil_threshold,
        discolor_threshold=body.discolor_threshold,
        scratch_threshold=body.scratch_threshold,
        capture_recipe=body.capture_recipe,
        expected_count=body.expected_count,
        outer_diameter_mm=body.outer_diameter_mm,
        version=1,
        updated_by=user.username,
        updated_at=datetime.now(timezone.utc),
    )
    db.add(row)
    write_log(
        db,
        category=LogCategory.USER,
        message=f"master.create {body.item_code} by={user.username}",
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return item_to_schema(row)


@router.put("/{item_code}", response_model=ItemMasterSchema)
def update_item(
    item_code: str,
    body: ItemMasterUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_min_role(Role.QUALITY)),
):
    """부분 갱신. 변경 시 version 증가 + updated_by/at 기록(변경 이력)."""
    row = db.get(ItemMaster, item_code)
    if not row:
        raise HTTPException(status_code=404, detail="품목 없음")
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="변경할 항목 없음")
    for field, value in changes.items():
        setattr(row, field, value)
    row.version = (row.version or 1) + 1
    row.updated_by = user.username
    row.updated_at = datetime.now(timezone.utc)
    write_log(
        db,
        category=LogCategory.USER,
        message=f"master.update {item_code} v{row.version} by={user.username}",
        payload={"changes": list(changes.keys())},
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return item_to_schema(row)




@router.put("/{item_code}/spec", response_model=ItemMasterSchema)
def update_item_spec(
    item_code: str,
    body: ItemSpecUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_min_role(_SPEC_EDIT_MIN_ROLE)),
):
    """오더 교체용 치수 사양 변경 — 기준길이·공차만.

    전체 갱신(PUT /{item_code})과 달리 px→mm 보정계수나 표면 임계값은 건드릴
    수 없다. 라인에서 급히 바꾸다 엉뚱한 값을 흔드는 사고를 막기 위함이다.

    감사 로그에 **이전값과 새값을 함께** 남긴다. 판정 결과가 이상할 때 "언제
    누가 무엇을 얼마에서 얼마로 바꿨나" 를 되짚을 수 있어야 한다.
    """
    row = db.get(ItemMaster, item_code)
    if not row:
        raise HTTPException(status_code=404, detail="품목 없음")

    before = {
        "ref_length_mm": float(row.ref_length_mm),
        "tol_plus_mm": float(row.tol_plus_mm),
        "tol_minus_mm": float(row.tol_minus_mm),
    }
    after = {
        "ref_length_mm": float(body.ref_length_mm),
        "tol_plus_mm": float(body.tol_plus_mm),
        "tol_minus_mm": float(body.tol_minus_mm),
    }
    if body.expected_count is not None:
        before["expected_count"] = int(row.expected_count or 1)
        after["expected_count"] = int(body.expected_count)
    if before == after:
        # 값이 같으면 version 을 올리지 않는다 — 이력이 의미 없는 줄로 불어난다.
        return item_to_schema(row)

    row.ref_length_mm = after["ref_length_mm"]
    row.tol_plus_mm = after["tol_plus_mm"]
    row.tol_minus_mm = after["tol_minus_mm"]
    if body.expected_count is not None:
        row.expected_count = int(body.expected_count)
    row.version = (row.version or 1) + 1
    row.updated_by = user.username
    row.updated_at = datetime.now(timezone.utc)
    write_log(
        db,
        category=LogCategory.USER,
        message=(
            f"master.spec {item_code} v{row.version} "
            f"{before['ref_length_mm']}→{after['ref_length_mm']}mm "
            f"(+{after['tol_plus_mm']}/-{after['tol_minus_mm']}) by={user.username}"
        ),
        payload={"before": before, "after": after},
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return item_to_schema(row)


@router.post("/{item_code}/calibrate", response_model=ItemMasterSchema)
def calibrate_item(
    item_code: str,
    body: CalibrationRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_min_role(Role.QUALITY)),
):
    """웹 자기보정: px_to_mm_scale := 기존 scale × (actual_mm / measured_mm).

    기준품(알려진 실제 길이)을 검사해 시스템 측정값 measured_mm 와 실제값
    actual_mm 을 입력하면 스케일을 보정한다. version 증가 + updated_by/at 기록.
    measured_mm/actual_mm ≤ 0 은 스키마(gt=0)에서 422로 거부.
    """
    row = db.get(ItemMaster, item_code)
    if not row:
        raise HTTPException(status_code=404, detail="품목 없음")
    old_scale = float(row.px_to_mm_scale)
    new_scale = old_scale * (body.actual_mm / body.measured_mm)
    row.px_to_mm_scale = new_scale
    row.version = (row.version or 1) + 1
    row.updated_by = user.username
    row.updated_at = datetime.now(timezone.utc)
    write_log(
        db,
        category=LogCategory.USER,
        message=f"master.calibrate {item_code} v{row.version} by={user.username}",
        payload={
            "old_scale": old_scale,
            "new_scale": new_scale,
            "measured_mm": body.measured_mm,
            "actual_mm": body.actual_mm,
        },
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return item_to_schema(row)


@router.delete("/{item_code}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(
    item_code: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_min_role(Role.ADMIN)),
):
    """품목 삭제(관리자 전용)."""
    row = db.get(ItemMaster, item_code)
    if not row:
        raise HTTPException(status_code=404, detail="품목 없음")
    db.delete(row)
    write_log(
        db,
        category=LogCategory.USER,
        message=f"master.delete {item_code} by={user.username}",
        commit=False,
    )
    db.commit()
