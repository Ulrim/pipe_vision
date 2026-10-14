"""AIVIS → MES 수신 (MES_INTERFACE §2·§3).

지키는 것:
1. 가져간 행만 `consumed=true` 로 표시한다 — 이것이 AIVIS KPI 의 'MES 수신 확인'.
2. MES 적재가 커밋된 뒤에만 표시한다(먼저 표시하고 적재에 실패하면 그 건은 사라진다).
3. 같은 멱등키는 갱신한다 — 재확인 결과가 바뀌면 AIVIS 가 다시 내놓는다.
4. 읽은 뒤 AIVIS 가 그 행을 고쳤으면 표시하지 않는다(다음 회차에 새 값으로 다시 읽는다).
5. 예전 AIVIS(새 컬럼 없는 테이블)도 읽는다.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

from sqlalchemy import MetaData, create_engine, select, update

from mes_app import intake as intake_mod
from mes_app.db import SessionLocal
from mes_app.intake import InterfaceReader
from mes_app.models import QualityRecord, WorkOrder

from .conftest import IFACE, IFACE_ENGINE, iface_row, iface_table, insert_iface, token


def _iface_rows():
    with IFACE_ENGINE.connect() as conn:
        return [dict(r) for r in conn.execute(select(IFACE).order_by(IFACE.c.id)).mappings()]


def _records():
    db = SessionLocal()
    try:
        return list(db.execute(select(QualityRecord).order_by(QualityRecord.id)).scalars())
    finally:
        db.close()


def test_poll_stores_and_marks_consumed(client):
    insert_iface(iface_row(1), iface_row(2, final_verdict="NG", defect_codes=["LEN"]),
                 iface_row(3, inspection_stage="CRATE_COUNT", count_detected=18,
                           count_expected=20, final_verdict="NG", defect_codes=["COUNT"]))
    r = client.post("/intake/poll", headers=token())
    assert r.status_code == 200, r.text
    assert r.json() == {"scanned": 3, "stored": 3, "updated": 0, "duplicate": 0,
                        "marked": 3, "error": None}

    rows = _iface_rows()
    assert all(x["consumed"] for x in rows), "가져간 행은 consumed=true"
    assert all(x["consumed_at"] is not None for x in rows)

    recs = _records()
    assert [x.final_verdict for x in recs] == ["OK", "NG", "NG"]
    assert recs[1].defect_codes == ["LEN"]
    assert recs[2].count_detected == 18 and recs[2].inspection_stage == "CRATE_COUNT"
    assert recs[0].meas_length_mm == 250.01 and recs[0].source == "table"

    again = client.post("/intake/poll", headers=token()).json()
    assert again["scanned"] == 0, "이미 가져간 것은 다시 읽지 않는다"


def test_review_change_is_pulled_again_and_updates(client):
    """AIVIS 재확인(AI NG → 사람 OK) → 행이 consumed=false 로 돌아오면 MES 가 갱신한다."""
    insert_iface(iface_row(1, final_verdict="NG", defect_codes=["SCR"]))
    client.post("/intake/poll", headers=token())
    with IFACE_ENGINE.begin() as conn:
        conn.execute(update(IFACE).values(
            manual_verdict="OK", consumed=False, updated_at=datetime.now(timezone.utc)))

    res = client.post("/intake/poll", headers=token()).json()
    assert (res["updated"], res["stored"], res["marked"]) == (1, 0, 1)
    rec = _records()[0]
    assert rec.manual_verdict == "OK" and rec.updated_at is not None


def test_row_changed_while_reading_is_not_marked(client, monkeypatch):
    """읽은 뒤 AIVIS 가 그 행을 고치면(재확인) 표시하지 않는다 — 고친 값이 MES 로 안 가면 안 된다."""
    insert_iface(iface_row(1, final_verdict="NG", defect_codes=["OIL"]))
    real = intake_mod.autostart_work_orders

    def concurrent_review(db, records):
        with IFACE_ENGINE.begin() as conn:  # MES 가 적재하는 사이 작업자가 재확인했다
            conn.execute(update(IFACE).values(
                manual_verdict="OK", updated_at=datetime.now(timezone.utc)))
        return real(db, records)

    monkeypatch.setattr(intake_mod, "autostart_work_orders", concurrent_review)
    res = client.post("/intake/poll", headers=token()).json()
    assert (res["stored"], res["marked"]) == (1, 0)
    assert _iface_rows()[0]["consumed"] in (False, 0)

    monkeypatch.setattr(intake_mod, "autostart_work_orders", real)
    res = client.post("/intake/poll", headers=token()).json()
    assert (res["updated"], res["marked"]) == (1, 1)
    assert _records()[0].manual_verdict == "OK"


def test_old_aivis_table_without_new_columns_still_works():
    """새 컬럼(단계·재확인·개수·consumed_at)이 없는 예전 테이블도 읽고 표시한다."""
    d = tempfile.mkdtemp(prefix="mes_old_iface_")
    eng = create_engine(f"sqlite:///{os.path.join(d, 'old.db')}", future=True)
    t = iface_table(MetaData(), new_columns=False)
    t.metadata.create_all(eng)
    row = iface_row(1)
    for k in ("inspection_stage", "tube_index"):
        row.pop(k)
    with eng.begin() as conn:
        conn.execute(t.insert(), [row])

    db = SessionLocal()
    try:
        res = InterfaceReader(eng).poll_once(db)
    finally:
        db.close()
    assert (res.stored, res.marked, res.error) == (1, 1, None)
    rec = _records()[0]
    assert rec.inspection_stage is None and rec.lot == "L-1"


def test_first_result_starts_planned_work_order(client):
    r = client.post("/work-orders", headers=token(), json={
        "item_code": "HP12", "lot": "L-AUTO", "plan_qty": 20})
    assert r.status_code == 201, r.text
    insert_iface(iface_row(1, lot="L-AUTO"))
    client.post("/intake/poll", headers=token())
    db = SessionLocal()
    try:
        wo = db.execute(select(WorkOrder).where(WorkOrder.lot == "L-AUTO")).scalar_one()
        assert wo.status == "RUNNING" and wo.started_at is not None
    finally:
        db.close()


def test_rest_intake_store_duplicate_update(client):
    body = {
        "idem_key": "L-R|HP12|2026-10-14T00:00:01+00:00|CAM2|0", "lot": "L-R",
        "item_code": "HP12", "inspected_at": "2026-10-14T00:00:01+00:00", "cam_id": "CAM2",
        "final_verdict": "NG", "defect_codes": ["DIS"], "inspection_stage": "POST_WASH_SURFACE",
        "unknown_field": 1,
    }
    assert client.post("/intake/quality", json=body).json()["status"] == "stored"
    assert client.post("/intake/quality", json=body).json()["status"] == "duplicate"
    upd = client.post("/intake/quality", json={**body, "manual_verdict": "OK"}).json()
    assert upd["status"] == "updated"
    rec = _records()[0]
    assert rec.source == "rest" and rec.manual_verdict == "OK"


def test_rest_idem_key_from_header(client):
    body = {"lot": "L-H", "inspected_at": "2026-10-14T00:00:02+00:00", "final_verdict": "OK"}
    assert client.post("/intake/quality", json=body).status_code == 422
    r = client.post("/intake/quality", json=body, headers={"X-Idempotency-Key": "K-1"})
    assert r.json() == {"status": "stored", "idem_key": "K-1"}


def test_rest_intake_token(client, monkeypatch):
    from mes_app.config import get_settings

    monkeypatch.setattr(get_settings(), "intake_token", "s3cret")
    body = {"idem_key": "K-T", "lot": "L-T", "inspected_at": "2026-10-14T00:00:03+00:00",
            "final_verdict": "OK"}
    assert client.post("/intake/quality", json=body).status_code == 401
    ok = client.post("/intake/quality", json=body, headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200


def test_intake_status_and_permissions(client):
    insert_iface(iface_row(1), iface_row(2))
    st = client.get("/intake/status", headers=token("op1", "operator")).json()
    assert st["table_enabled"] is True and st["interface_pending"] == 2
    assert st["records_total"] == 0

    assert client.post("/intake/poll", headers=token("op1", "operator")).status_code == 403
    client.post("/intake/poll", headers=token())
    st = client.get("/intake/status", headers=token("op1", "operator")).json()
    assert st["interface_pending"] == 0 and st["records_total"] == 2
    assert st["records_today"] == 2 and st["last_result"]["stored"] == 2
    assert st["runs"][0]["source"] == "table" and st["runs"][0]["received"] == 2

    q = client.get("/quality", headers=token("op1", "operator"), params={"lot": "L-1"}).json()
    assert len(q) == 2 and q[0]["inspected_at"] > q[1]["inspected_at"]


def test_requires_aivis_login(client):
    assert client.get("/work-orders").status_code == 401
    assert client.get("/work-orders", headers={"Authorization": "Bearer junk"}).status_code == 401
