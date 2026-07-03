"""sys_log 적재 헬퍼 (CLAUDE.md §5 M15)."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from aivis_types import LogCategory
from db.models import SysLog


def write_log(
    db: Session,
    *,
    category: LogCategory,
    message: str,
    level: str = "INFO",
    payload: dict[str, Any] | None = None,
    commit: bool = True,
) -> None:
    """sys_log 한 줄을 적재한다. category 는 LogCategory enum."""
    row = SysLog(
        level=level,
        category=category.value if isinstance(category, LogCategory) else str(category),
        message=message,
        payload=payload,
    )
    db.add(row)
    if commit:
        db.commit()
