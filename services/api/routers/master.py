"""기준정보 관리 라우터 (CLAUDE.md §5 M13, §7.4 CRUD /master/items).

변경 시 version 자동 증가, 수정 권한은 품질관리자/관리자(quality+)로 제한.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from aivis_types import (
    CalibrationRequest,
    ItemMaster as ItemMasterSchema,
    ItemMasterCreate,
    ItemMasterUpdate,
    LogCategory,
    Role,
)

from core.logging import write_log
from core.security import CurrentUser, require_min_role
from db.base import get_db
from db.models import ItemMaster
from db.serialize import item_to_schema

router = APIRouter(prefix="/master/items", tags=["master"])


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
