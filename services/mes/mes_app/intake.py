"""AIVIS 검사결과 수신 (docs/MES_INTERFACE.md §2·§3).

두 길이 있고 둘 다 같은 upsert 로 모인다.

1. **테이블 폴링(기본)** — AIVIS DB 의 `mes_quality_if` 에서 `consumed=false` 를
   읽어 MES 품질이력에 적재하고, **적재가 커밋된 뒤에** `consumed=true` 로 표시한다.
   이 표시가 AIVIS KPI 의 'MES 수신 확인' 이다 — 자기 테이블 적재율이 아니라
   상대가 실제로 가져갔다는 증거(2026-10-10 점검 지적).
2. **REST 수신** — AIVIS 가 `MES_MODE=rest` 일 때 `POST /intake/quality` 로 보낸다.

같은 멱등키가 다시 오면 **갱신**한다(무시하지 않는다). AIVIS 는 재확인 결과가
바뀌면 그 행을 `consumed=false` 로 되돌리거나(테이블) 다시 보낸다(REST) — 그 값으로
생산실적의 양품 수량이 바뀌기 때문이다.

인터페이스 테이블은 **반영(reflection)** 으로 읽는다. AIVIS 를 먼저 올린 현장(예전
컬럼만 있는 테이블)에서도 있는 컬럼만 읽고 동작한다.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import MetaData, Table, and_, false, func, or_, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from mes_app.models import IntakeRun, QualityRecord, WorkOrder

log = logging.getLogger("mes.intake")

#: 다시 받으면 바뀔 수 있는 값(재확인·작업지시 보정). 멱등키 구성값은 바뀌지 않는다.
MUTABLE = (
    "final_verdict", "defect_codes", "manual_verdict", "work_order",
    "count_detected", "count_expected", "meas_length_mm", "deviation_mm",
    "inspection_stage", "tube_index", "item_code", "inspection_id",
)
_FIELDS = (
    "idem_key", "inspection_id", "lot", "work_order", "item_code", "cam_id",
    "inspection_stage", "tube_index", "inspected_at", "final_verdict", "defect_codes",
    "meas_length_mm", "deviation_mm", "manual_verdict", "count_detected", "count_expected",
)
#: 수신 기록 보관 개수(오래된 것부터 지운다).
_KEEP_RUNS = 500


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(ts: Any) -> Any:
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    if isinstance(ts, datetime) and ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def _num(v: Any) -> Optional[float]:
    return None if v is None else float(v)


def _codes(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):  # 드라이버가 JSON 을 문자열로 줄 때
        import json

        try:
            v = json.loads(v)
        except ValueError:
            v = [c for c in v.strip("{}").split(",") if c]
    return [str(c) for c in v]


def normalize(data: dict[str, Any]) -> dict[str, Any]:
    """인터페이스 행/REST 본문 → 품질이력 필드. 없는 키는 None."""
    out = {k: data.get(k) for k in _FIELDS}
    out["inspected_at"] = _aware(out["inspected_at"])
    out["defect_codes"] = _codes(out["defect_codes"])
    out["meas_length_mm"] = _num(out["meas_length_mm"])
    out["deviation_mm"] = _num(out["deviation_mm"])
    for k in ("final_verdict", "manual_verdict"):
        if out[k] is not None:
            out[k] = str(getattr(out[k], "value", out[k])).upper()
    return out


def upsert_record(db: Session, data: dict[str, Any], *, source: str) -> str:
    """품질이력 1건 upsert → 'stored' | 'updated' | 'duplicate'.

    REST 와 폴링이 같은 키를 동시에 넣는 경우(UNIQUE 충돌)는 세이브포인트로 되돌리고
    갱신으로 다시 처리한다.
    """
    rec = normalize(data)
    if not rec["idem_key"] or not rec["lot"] or rec["inspected_at"] is None:
        raise ValueError("idem_key·lot·inspected_at 은 필수다")
    for attempt in (1, 2):
        existing = db.execute(
            select(QualityRecord).where(QualityRecord.idem_key == rec["idem_key"])
        ).scalar_one_or_none()
        if existing is None:
            try:
                with db.begin_nested():
                    db.add(QualityRecord(**rec, source=source))
                return "stored"
            except IntegrityError:
                if attempt == 2:
                    raise
                continue
        changed = False
        for k in MUTABLE:
            new = rec.get(k)
            if new is None and k not in ("manual_verdict", "work_order"):
                continue  # 예전 AIVIS 가 안 보내는 값으로 덮어쓰지 않는다
            if getattr(existing, k) != new:
                setattr(existing, k, new)
                changed = True
        if changed:
            existing.updated_at = _utcnow()
            return "updated"
        return "duplicate"
    return "duplicate"  # pragma: no cover


def autostart_work_orders(db: Session, records: Iterable[dict[str, Any]]) -> int:
    """검사결과가 처음 들어온 작업지시를 PLANNED → RUNNING 으로(시작 시각 = 첫 검사)."""
    first: dict[tuple[str, Optional[str]], datetime] = {}
    for r in records:
        key = (r["lot"], r.get("work_order"))
        ts = _aware(r["inspected_at"])
        if key not in first or ts < first[key]:
            first[key] = ts
    started = 0
    for (lot, wo_no), ts in first.items():
        conds = [WorkOrder.lot == lot]
        if wo_no:
            conds.append(WorkOrder.wo_no == wo_no)
        for wo in db.execute(
            select(WorkOrder).where(or_(*conds), WorkOrder.status == "PLANNED")
        ).scalars():
            wo.status = "RUNNING"
            wo.started_at = ts
            started += 1
    return started


def _record_run(db: Session, source: str, received: int, updated: int,
                error: Optional[str]) -> None:
    db.add(IntakeRun(ts=_utcnow(), source=source, received=received, updated=updated,
                     error=error))
    old = db.execute(
        select(IntakeRun.id).order_by(IntakeRun.id.desc()).offset(_KEEP_RUNS)
    ).scalars().all()
    if old:
        db.query(IntakeRun).filter(IntakeRun.id.in_(old)).delete(synchronize_session=False)


# ---- 테이블 폴링 ------------------------------------------------------------------

@dataclass
class PollResult:
    scanned: int = 0
    stored: int = 0
    updated: int = 0
    duplicate: int = 0
    marked: int = 0
    error: Optional[str] = None
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in
                ("scanned", "stored", "updated", "duplicate", "marked", "error")}


class InterfaceReader:
    """AIVIS `mes_quality_if` 반영(reflection) 캐시 + 폴링."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._table: Optional[Table] = None

    def table(self) -> Table:
        if self._table is None:
            self._table = Table("mes_quality_if", MetaData(), autoload_with=self.engine)
        return self._table

    def reset(self) -> None:
        self._table = None

    def pending_count(self) -> int:
        t = self.table()
        with self.engine.connect() as conn:
            return int(conn.execute(
                select(func.count()).select_from(t).where(t.c.consumed == false())
            ).scalar() or 0)

    def poll_once(self, db: Session, *, batch: int = 500) -> PollResult:
        res = PollResult()
        try:
            t = self.table()
        except SQLAlchemyError as exc:
            res.error = f"인터페이스 테이블을 열 수 없다: {exc.__class__.__name__}: {exc}"
            return res
        cols = t.c
        try:
            with self.engine.connect() as conn:
                rows = [dict(r) for r in conn.execute(
                    select(t).where(cols.consumed == false()).order_by(cols.id).limit(batch)
                ).mappings()]
        except SQLAlchemyError as exc:
            self.reset()  # 스키마가 바뀌었을 수 있다 — 다음 회차에 다시 반영
            res.error = f"인터페이스 조회 실패: {exc.__class__.__name__}: {exc}"
            return res
        res.scanned = len(rows)
        if not rows:
            return res

        # 1) MES 적재 — 한 트랜잭션
        try:
            for r in rows:
                status = upsert_record(db, r, source="table")
                setattr(res, status, getattr(res, status) + 1)
            autostart_work_orders(db, rows)
            db.commit()
        except (SQLAlchemyError, ValueError) as exc:
            db.rollback()
            res.error = f"MES 적재 실패(인터페이스는 그대로 둔다): {exc}"
            return res

        # 2) 적재가 커밋된 뒤에만 '가져갔음' 표시. 읽은 뒤 AIVIS 가 그 행을 고쳤으면
        #    (재확인 → updated_at 변경) 표시하지 않는다 — 다음 회차에 새 값으로 다시 읽는다.
        now = _utcnow()
        values: dict[str, Any] = {"consumed": True}
        if "consumed_at" in cols:
            values["consumed_at"] = now
        try:
            with self.engine.begin() as conn:
                if "updated_at" in cols:
                    for r in rows:
                        cond = [cols.id == r["id"], cols.consumed == false()]
                        u = r.get("updated_at")
                        cond.append(cols.updated_at.is_(None) if u is None else cols.updated_at == u)
                        res.marked += conn.execute(
                            update(t).where(and_(*cond)).values(**values)
                        ).rowcount or 0
                else:
                    res.marked = conn.execute(
                        update(t).where(cols.id.in_([r["id"] for r in rows]),
                                        cols.consumed == false()).values(**values)
                    ).rowcount or 0
        except SQLAlchemyError as exc:
            # MES 에는 이미 들어갔다. 다음 회차에 같은 키로 다시 읽혀 'duplicate' 로 끝난다.
            res.error = f"consumed 표시 실패(다음 회차에 재시도): {exc}"
        return res


class Poller:
    """백그라운드 폴링 스레드. 상태는 연계 현황 화면이 읽는다."""

    def __init__(self, reader: InterfaceReader, session_factory, *, interval_s: float,
                 batch: int) -> None:
        self.reader = reader
        self.session_factory = session_factory
        self.interval_s = max(1.0, interval_s)
        self.batch = batch
        self.last_poll_at: Optional[datetime] = None
        self.last_result: Optional[PollResult] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def run_once(self) -> PollResult:
        with self._lock:  # 수동 '지금 가져오기' 와 주기 실행이 겹치지 않게
            db = self.session_factory()
            try:
                res = self.reader.poll_once(db, batch=self.batch)
                if res.scanned or res.error:
                    _record_run(db, "table", res.stored, res.updated, res.error)
                    db.commit()
            finally:
                db.close()
            self.last_poll_at = _utcnow()
            self.last_result = res
            if res.error:
                log.warning("MES 수신: %s", res.error)
            return res

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as exc:  # noqa: BLE001 - 스레드는 죽으면 안 된다
                log.exception("MES 폴링 예외: %s", exc)
            self._stop.wait(self.interval_s)

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="mes-intake", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None


def record_rest_run(db: Session, status: str, error: Optional[str] = None) -> None:
    _record_run(db, "rest", 1 if status == "stored" else 0,
                1 if status == "updated" else 0, error)
