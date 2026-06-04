"""로그 조회 라우터 (CLAUDE.md §5 M15, §7.4 GET /logs?category=)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from aivis_types import LogCategory, Role, SysLog as SysLogSchema

from core.security import CurrentUser, require_min_role
from db.base import get_db
from db.models import SysLog

router = APIRouter(prefix="/logs", tags=["logs"])


@router.get("", response_model=list[SysLogSchema])
def list_logs(
    db: Session = Depends(get_db),
    category: Optional[LogCategory] = Query(None),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    _user: CurrentUser = Depends(require_min_role(Role.QUALITY)),
):
    """로그 조회(품질관리자 이상). category 필터(inspect/db/mes/error/user)."""
    stmt = select(SysLog)
    if category:
        stmt = stmt.where(SysLog.category == category.value)
    stmt = stmt.order_by(SysLog.id.desc()).limit(limit).offset(offset)
    rows = db.execute(stmt).scalars().all()
    return [
        SysLogSchema(
            id=r.id,
            ts=r.ts,
            level=r.level,
            category=r.category or LogCategory.INSPECT.value,
            message=r.message,
            payload=r.payload,
        )
        for r in rows
    ]
