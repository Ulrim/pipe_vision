"""품질·생산실적 MES 연계 보강 (2026-10-14).

1. 개수 확인 결과(실제 개수/기준 개수)가 DB 와 연계 행에 남는다 — MES 포장 실적.
2. 연계 행에 단계·튜브 번호·재확인 결과가 실린다 — MES 단계별 실적.
3. 재확인 결과가 바뀌면 연계 행을 되돌린다(table: consumed=false, rest: 재전송).
4. KPI 수기 입력은 보낸 항목만 바꾼다 — MES 반영이 작업공수·비고를 지우지 않게.
5. REST 연계에 수신 토큰을 싣는다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from db.base import SessionLocal
from db.models import Inspection, MesQualityIf
from mes.adapter import _row_to_payload, restage_after_review
from mes.config import MesConfig
from mes.transport import HttpxMesTransport

_ITEM = "HPMES"
_T0 = datetime(2035, 3, 2, 1, 0, tzinfo=timezone.utc)


def _item(client, auth):
    r = client.post("/master/items", headers=auth("qa1"), json={
        "item_code": _ITEM, "item_name": "mes", "ref_length_mm": 250.0,
        "tol_plus_mm": 0.1, "tol_minus_mm": 0.1, "px_to_mm_scale": 0.1, "expected_count": 20,
    })
    assert r.status_code in (201, 409), r.text


def _post(client, i: int, **over) -> dict:
    body = {
        "lot": "MES-L1", "item_code": _ITEM, "cam_id": "MES-CAM",
        "inspected_at": (_T0 + timedelta(seconds=i)).isoformat(), "final_verdict": "OK",
        "defect_codes": [], "review_flag": False, "mes_synced": False, "tube_index": 0,
    }
    body.update(over)
    r = client.post("/inspection", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _staged(insp_id: int) -> MesQualityIf:
    db = SessionLocal()
    try:
        return db.query(MesQualityIf).filter(MesQualityIf.inspection_id == insp_id).one()
    finally:
        db.close()


def test_crate_count_is_stored_and_staged(client, auth):
    _item(client, auth)
    got = _post(client, 1, inspection_stage="CRATE_COUNT", final_verdict="NG",
                defect_codes=["COUNT"], count_detected=18, count_expected=20, review_flag=True)
    again = client.get(f"/inspection/{got['id']}", headers=auth("op1")).json()
    assert (again["count_detected"], again["count_expected"]) == (18, 20)
    # table 모드: 스테이징 적재 = 연계됨(같은 트랜잭션). 종전엔 워치독을 기다렸는데
    # 워치독이 기동되지 않아 연계율이 0% 로 남았다.
    assert again["mes_synced"] is True

    s = _staged(got["id"])
    assert (s.inspection_stage, s.count_detected, s.count_expected) == ("CRATE_COUNT", 18, 20)
    assert s.tube_index == 0 and s.consumed is False


def test_bundle_tube_index_and_stage_are_staged(client, auth):
    _item(client, auth)
    got = _post(client, 2, tube_index=7, inspection_stage="POST_WASH_SURFACE",
                final_verdict="NG", defect_codes=["OIL"])
    s = _staged(got["id"])
    assert (s.tube_index, s.inspection_stage, s.defect_codes) == (7, "POST_WASH_SURFACE", ["OIL"])


def test_review_resets_staging_for_mes_to_pull_again(client, auth):
    """MES 가 가져간 뒤 작업자가 재확인(AI NG → 사람 OK) — 연계 행이 다시 나와야 한다."""
    _item(client, auth)
    got = _post(client, 3, final_verdict="NG", defect_codes=["LEN"], review_flag=True)
    db = SessionLocal()
    try:  # MES 가 가져갔다
        db.query(MesQualityIf).filter(MesQualityIf.inspection_id == got["id"]).update(
            {"consumed": True, "consumed_at": datetime.now(timezone.utc)})
        db.commit()
    finally:
        db.close()

    r = client.patch(f"/inspection/{got['id']}/review", headers=auth("op1"),
                     json={"manual_verdict": "OK"})
    assert r.status_code == 200, r.text
    s = _staged(got["id"])
    assert s.manual_verdict == "OK"
    assert s.consumed is False and s.consumed_at is None
    assert s.updated_at is not None


def test_review_in_rest_mode_resends():
    db = SessionLocal()
    try:
        row = Inspection(lot="MES-REST", item_code=None, cam_id="MES-R",
                         inspected_at=_T0 + timedelta(seconds=9), final_verdict="NG",
                         defect_codes=["SCR"], mes_synced=True, manual_verdict="OK")
        db.add(row)
        db.flush()
        cfg = MesConfig(mode="rest", rest_url="http://mes", rest_timeout_s=1, idem_header="X",
                        watchdog_interval_s=1, watchdog_batch_size=1, max_retry=1,
                        backoff_base_s=0.1, backoff_max_s=0.1)
        restage_after_review(db, row, cfg)
        assert row.mes_synced is False, "워치독이 재확인 값을 실어 다시 보낸다"

        train = Inspection(lot="TRAIN-X", item_code=None, cam_id="MES-R",
                           inspected_at=_T0 + timedelta(seconds=10), final_verdict="NG",
                           defect_codes=[], mes_synced=True, manual_verdict="OK")
        db.add(train)
        db.flush()
        restage_after_review(db, train, cfg)
        assert train.mes_synced is True, "수집용 LOT 은 MES 로 가지 않는다"
    finally:
        db.rollback()
        db.close()


def test_rest_payload_carries_new_fields():
    row = Inspection(id=5, lot="L", item_code="HP", cam_id="C", inspected_at=_T0,
                     final_verdict="NG", defect_codes=["COUNT"], inspection_stage="CRATE_COUNT",
                     tube_index=0, manual_verdict=None, count_detected=19, count_expected=20)
    p = _row_to_payload(row, "K")
    assert p["idem_key"] == "K" and p["inspected_at"] == _T0.isoformat()
    assert (p["inspection_stage"], p["count_detected"], p["count_expected"]) == (
        "CRATE_COUNT", 19, 20)
    assert "manual_verdict" in p and p["tube_index"] == 0


def test_rest_transport_sends_token():
    seen = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"status": "stored"}

    class _Client:
        def post(self, url, json, headers):
            seen.update(headers)
            return _Resp()

    HttpxMesTransport("http://mes/intake/quality", client=_Client(), token="t0k").send(
        {"lot": "L"}, idem_key="K")
    assert seen["Authorization"] == "Bearer t0k" and seen["X-Idempotency-Key"] == "K"

    seen.clear()
    HttpxMesTransport("http://mes", client=_Client()).send({}, idem_key="K")
    assert "Authorization" not in seen


def test_kpi_manual_updates_only_sent_fields(client, auth):
    h = auth("qa1")
    full = {"period": "2035-04-01", "claim_count": 1, "workload_index": 55.0,
            "lead_time_days": 6.0, "shipped_qty": 1000, "leak_defect_qty": 2, "note": "수기"}
    assert client.post("/kpi/manual", headers=h, json=full).status_code == 200

    # MES 반영: 네 값만 보낸다 — 작업공수·비고는 그대로여야 한다.
    r = client.post("/kpi/manual", headers=h, json={
        "period": "2035-04-01", "claim_count": 3, "lead_time_days": 5.5,
        "shipped_qty": 1200, "leak_defect_qty": 4})
    body = r.json()
    assert (body["claim_count"], body["shipped_qty"], body["lead_time_days"]) == (3, 1200, 5.5)
    assert body["workload_index"] == 55.0 and body["note"] == "수기"

    # 명시적 null 은 지운다.
    body = client.post("/kpi/manual", headers=h,
                       json={"period": "2035-04-01", "workload_index": None}).json()
    assert body["workload_index"] is None and body["claim_count"] == 3


def test_api_process_runs_mes_watchdog(monkeypatch):
    """워치독이 API 와 함께 뜬다 — 스테이징이 빠진 행(로컬 큐 복구분 등)을 보충한다."""
    import time

    import main

    monkeypatch.setenv("MES_WATCHDOG_ENABLED", "true")
    monkeypatch.setenv("MES_WATCHDOG_BATCH", "5000")
    db = SessionLocal()
    try:
        row = Inspection(lot="WD-1", item_code=None, cam_id="WD-CAM",
                         inspected_at=_T0 + timedelta(seconds=50), final_verdict="OK",
                         defect_codes=[], mes_synced=False)
        db.add(row)
        db.commit()
        rid = row.id
    finally:
        db.close()

    th, stop = main._start_mes_watchdog()
    try:
        assert th is not None and th.is_alive()
        for _ in range(100):
            db = SessionLocal()
            try:
                if db.get(Inspection, rid).mes_synced:
                    break
            finally:
                db.close()
            time.sleep(0.05)
    finally:
        stop.set()
        th.join(timeout=5)
    assert not th.is_alive(), "종료 신호에 바로 멈춘다"
    db = SessionLocal()
    try:
        assert db.get(Inspection, rid).mes_synced is True
        assert db.query(MesQualityIf).filter(MesQualityIf.inspection_id == rid).count() == 1
    finally:
        # 품목 없는 시험 행을 남기면 다른 시험의 조회(직렬화)가 깨진다 — 지운다.
        db.query(MesQualityIf).filter(MesQualityIf.inspection_id == rid).delete()
        db.query(Inspection).filter(Inspection.id == rid).delete()
        db.commit()
        db.close()

    monkeypatch.setenv("MES_WATCHDOG_ENABLED", "false")
    assert main._start_mes_watchdog() == (None, None)


def test_order_push_keeps_global_mode(client, auth):
    """MES 가 품목·LOT·작업지시만 내려도 전역 검사 모드는 그대로다(명시적 null 만 지운다)."""
    _item(client, auth)
    h = auth("qa1")
    try:
        r = client.put("/master/active", headers=h, json={
            "item_code": _ITEM, "lot": "A", "work_order": None,
            "inspection_stage": "POST_WASH_SURFACE"})
        assert r.json()["inspection_stage"] == "POST_WASH_SURFACE"
        r = client.put("/master/active", headers=h, json={
            "item_code": _ITEM, "lot": "WO-LOT", "work_order": "WO-1"})
        assert r.json()["inspection_stage"] == "POST_WASH_SURFACE"
        assert r.json()["lot"] == "WO-LOT"
        r = client.put("/master/active", headers=h, json={
            "item_code": _ITEM, "lot": "WO-LOT", "work_order": "WO-1",
            "inspection_stage": None})
        assert r.json()["inspection_stage"] is None
    finally:
        client.delete("/master/active", headers=h)

