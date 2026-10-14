"""월 KPI — 지금까지 손으로 넣던 값을 MES 기록에서 센다 (CLAUDE.md §1.1, docs/MES.md §5).

| 값 | 근거 |
|---|---|
| 출하수량 | 그 달 출하일의 출하 수량 합 |
| 리드타임(일) | 그 달 출하 중 수주에 이어진 것의 (출하일 − 수주일) 평균 |
| 납기 준수율(%) | 납기가 있는 수주의 출하 중 출하일 ≤ 납기 비율 |
| Claim 건수 | 그 달 발생일의 클레임 수 |
| 출하유출 부적합 수량 | 그 달 발생 클레임의 부적합 수량 합 |
| 출하유출불량률(ppm) | 부적합 수량 ÷ 출하수량 × 1,000,000 (출하 0 이면 없음) |

날짜는 사람이 입력한 현지(KST) 날짜다. AIVIS KPI 에 반영하는 것은 대시보드의
"KPI 에 반영" 버튼(AIVIS `POST /kpi/manual`, 이 네 값만 갱신)이다.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from mes_app.models import Claim, SalesOrder, Shipment


def month_bounds(period: str) -> tuple[date, date]:
    try:
        y, m = (int(x) for x in period.split("-"))
        start = date(y, m, 1)
    except (ValueError, TypeError) as exc:
        raise ValueError("period 는 YYYY-MM") from exc
    end = date(y + (m // 12), (m % 12) + 1, 1)
    return start, end


def monthly(db: Session, period: str) -> dict:
    start, end = month_bounds(period)
    ships = list(db.execute(
        select(Shipment).where(Shipment.ship_date >= start, Shipment.ship_date < end)
    ).scalars())
    so_ids = {s.so_id for s in ships if s.so_id}
    orders = {
        o.id: o for o in db.execute(
            select(SalesOrder).where(SalesOrder.id.in_(so_ids))
        ).scalars()
    } if so_ids else {}

    shipped_qty = sum(s.qty for s in ships)
    leads = [(s.ship_date - orders[s.so_id].order_date).days
             for s in ships if s.so_id in orders]
    due = [s for s in ships if s.so_id in orders and orders[s.so_id].due_date]
    on_time = sum(1 for s in due if s.ship_date <= orders[s.so_id].due_date)

    claims = list(db.execute(
        select(Claim).where(Claim.occurred_date >= start, Claim.occurred_date < end)
    ).scalars())
    leak = sum(c.defect_qty for c in claims)

    def _avg(xs: list[int]) -> Optional[float]:
        return round(sum(xs) / len(xs), 2) if xs else None

    return {
        "period": period,
        "shipment_count": len(ships),
        "shipped_qty": shipped_qty,
        "lead_time_days": _avg(leads),
        "lead_time_samples": len(leads),
        "on_time_rate_pct": round(on_time / len(due) * 100.0, 1) if due else None,
        "on_time_samples": len(due),
        "claim_count": len(claims),
        "leak_defect_qty": leak,
        "leak_ppm": round(leak / shipped_qty * 1_000_000, 1) if shipped_qty else None,
    }
