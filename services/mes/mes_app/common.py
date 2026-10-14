"""번호 매기기·날짜 도우미."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

KST = timezone(timedelta(hours=9))


def today_kst() -> date:
    return datetime.now(KST).date()


def next_no(db: Session, column, prefix: str, *, width: int = 3, on: date | None = None) -> str:
    """`{prefix}-YYYYMMDD-NNN`. 같은 날 같은 접두어 중 비어 있는 다음 번호."""
    day = (on or today_kst()).strftime("%Y%m%d")
    head = f"{prefix}-{day}-"
    n = int(db.execute(
        select(func.count()).where(column.like(f"{head}%"))
    ).scalar() or 0) + 1
    while True:
        cand = f"{head}{n:0{width}d}"
        if not db.execute(select(func.count()).where(column == cand)).scalar():
            return cand
        n += 1
