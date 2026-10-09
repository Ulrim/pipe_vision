"""스테이션(카메라)별 검사 모드 — 2대 구성 (2026-10-08).

오더(품목/LOT)는 두 스테이션이 같은 것을 보지만 모드는 다르다(컨베이어=길이,
크레이트=개수). 한 화면에서 모드를 바꿨는데 두 대가 같이 바뀌면 안 된다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core import heartbeat


def _wipe(client, auth):
    """테스트 DB 는 세션 공유 — 오더·스테이션 설정·하트비트를 전후로 비운다."""
    client.delete("/master/active", headers=auth("qa1"))
    for cam in ("PI-CAM1", "PI-CAM2"):
        client.delete(f"/master/stations/{cam}", headers=auth("op1"))
    heartbeat.reset()


@pytest.fixture(autouse=True)
def _clean(client, auth):
    _wipe(client, auth)
    yield
    _wipe(client, auth)


def _item(client, auth, code="HP12"):
    r = client.post("/master/items", headers=auth("qa1"), json={
        "item_code": code, "item_name": code, "ref_length_mm": 430.0,
        "tol_plus_mm": 0.1, "tol_minus_mm": 0.1, "px_to_mm_scale": 0.1,
        "expected_count": 20,
    })
    assert r.status_code in (201, 409), r.text


def _set(client, auth, stage, cam=None):
    body = {"item_code": "HP12", "inspection_stage": stage}
    if cam:
        body["cam_id"] = cam
    r = client.put("/master/active/stage", headers=auth("op1"), json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _active(client, auth, cam=None):
    params = {"cam_id": cam} if cam else None
    r = client.get("/master/active", headers=auth("op1"), params=params)
    assert r.status_code == 200
    return r.json()


def test_changing_one_station_does_not_change_the_other(client, auth):
    """핵심. 길이 스테이션을 개수로 바꿔도 크레이트 스테이션은 그대로여야 한다."""
    _item(client, auth)
    _set(client, auth, "CUT_LENGTH", cam="PI-CAM1")
    _set(client, auth, "CRATE_COUNT", cam="PI-CAM2")
    assert _active(client, auth, "PI-CAM1")["inspection_stage"] == "CUT_LENGTH"
    assert _active(client, auth, "PI-CAM2")["inspection_stage"] == "CRATE_COUNT"

    _set(client, auth, "POST_WASH_SURFACE", cam="PI-CAM1")
    assert _active(client, auth, "PI-CAM1")["inspection_stage"] == "POST_WASH_SURFACE"
    assert _active(client, auth, "PI-CAM2")["inspection_stage"] == "CRATE_COUNT", (
        "다른 스테이션이 따라 바뀌었다"
    )


def test_station_overrides_global_and_reports_its_source(client, auth):
    _item(client, auth)
    _set(client, auth, "CUT_LENGTH")                    # 전역
    _set(client, auth, "CRATE_COUNT", cam="PI-CAM2")    # 스테이션
    a1 = _active(client, auth, "PI-CAM1")
    a2 = _active(client, auth, "PI-CAM2")
    assert a1["inspection_stage"] == "CUT_LENGTH" and a1["stage_source"] == "order"
    assert a2["inspection_stage"] == "CRATE_COUNT" and a2["stage_source"] == "station"
    # cam_id 없이 보면 전역이 보인다(단일 구성 호환).
    assert _active(client, auth)["inspection_stage"] == "CUT_LENGTH"


def test_station_change_keeps_lot_and_global_stage(client, auth):
    _item(client, auth)
    client.put("/master/active", headers=auth("qa1"), json={
        "item_code": "HP12", "lot": "L-7", "inspection_stage": "CUT_LENGTH"})
    out = _set(client, auth, "CRATE_COUNT", cam="PI-CAM2")
    assert out["lot"] == "L-7"
    # 전역은 그대로 — 다른 스테이션은 여전히 길이.
    assert _active(client, auth, "PI-CAM1")["inspection_stage"] == "CUT_LENGTH"


def test_stations_listing(client, auth):
    _item(client, auth)
    _set(client, auth, "CUT_LENGTH", cam="PI-CAM1")
    _set(client, auth, "CRATE_COUNT", cam="PI-CAM2")
    r = client.get("/master/stations", headers=auth("op1"))
    assert r.status_code == 200
    got = {s["cam_id"]: s["inspection_stage"] for s in r.json()}
    assert got["PI-CAM1"] == "CUT_LENGTH" and got["PI-CAM2"] == "CRATE_COUNT"
    one = client.get("/master/stations/PI-CAM2", headers=auth("op1")).json()
    assert one["inspection_stage"] == "CRATE_COUNT"
    assert client.get("/master/stations/NOPE", headers=auth("op1")).json() is None
    # 해제하면 전역을 따른다(목록에서 빠지고 stage_source 가 order 로 돌아간다).
    assert client.delete("/master/stations/PI-CAM2", headers=auth("op1")).status_code == 204
    assert client.delete("/master/stations/PI-CAM2", headers=auth("op1")).status_code == 204
    got = {s["cam_id"] for s in client.get("/master/stations", headers=auth("op1")).json()}
    assert "PI-CAM2" not in got
    # 전역 모드는 안 정했으니 이제 PI-CAM2 는 모드 없음(워커는 env 로 떨어진다).
    a2 = _active(client, auth, "PI-CAM2")
    assert a2["inspection_stage"] is None and a2["stage_source"] is None


def test_audit_log_names_the_station(client, auth):
    from db.base import SessionLocal
    from db.models import SysLog

    _item(client, auth)
    _set(client, auth, "CRATE_COUNT", cam="PI-CAM2")
    db = SessionLocal()
    try:
        row = db.query(SysLog).filter(
            SysLog.message.like("master.active.stage%")).order_by(SysLog.id.desc()).first()
    finally:
        db.close()
    assert row is not None
    assert "station=PI-CAM2" in row.message
    assert (row.payload or {}).get("cam_id") == "PI-CAM2"


# ---- 카메라별 하트비트 → 모니터 ------------------------------------------------

def test_system_status_lists_each_station(client, auth):
    now = datetime.now(timezone.utc)
    heartbeat.record("PI-CAM1", now - timedelta(seconds=2), stage="CUT_LENGTH")
    heartbeat.record("PI-CAM2", now - timedelta(seconds=90), stage="CRATE_COUNT")
    r = client.get("/system/status", headers=auth("op1"))
    assert r.status_code == 200
    svc = r.json()["services"]
    workers = {w["cam_id"]: w for w in svc["workers"]}
    assert workers["PI-CAM1"]["state"] == "up"
    assert workers["PI-CAM1"]["stage"] == "CUT_LENGTH"
    assert workers["PI-CAM2"]["state"] == "down", "90초 전이면 down"
    assert workers["PI-CAM2"]["stage"] == "CRATE_COUNT"
    # 종전 단일 필드는 '가장 최근' 스테이션 기준 — 한 대가 죽어도 up 일 수 있다.
    # 그래서 2대 이상이면 workers 를 봐야 한다는 것이 이 테스트의 요점이다.
    assert svc["worker"] == "up"


def test_heartbeat_endpoint_records_stage_per_camera(client):
    for cam, stage in (("PI-CAM1", "CUT_LENGTH"), ("PI-CAM2", "CRATE_COUNT")):
        r = client.post("/inspection/status", json={
            "cam_id": cam, "item_code": "HP12", "expected": 1, "detected": 1,
            "ts": datetime.now(timezone.utc).isoformat(), "stage": stage,
        })
        assert r.status_code in (202, 401, 403)
    if r.status_code == 202:
        beats = {b.cam_id: b for b in heartbeat.all_beats()}
        assert beats["PI-CAM1"].stage == "CUT_LENGTH"
        assert beats["PI-CAM2"].stage == "CRATE_COUNT"


# ---- 이력 필터 / KPI ----------------------------------------------------------

def _post(client, **over):
    body = {
        "lot": "L1", "item_code": "HP12", "cam_id": "PI-CAM1",
        "inspected_at": datetime(2031, 4, 10, tzinfo=timezone.utc).isoformat(),
        "final_verdict": "OK", "defect_codes": [], "review_flag": False,
        "mes_synced": True, "proc_time_ms": 100, "tube_index": 0,
    }
    body.update(over)
    r = client.post("/inspection", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_list_filters_by_station_and_stage(client, auth):
    _item(client, auth)
    _post(client, lot="F1", cam_id="PI-CAM1", inspection_stage="CUT_LENGTH")
    _post(client, lot="F1", cam_id="PI-CAM2", inspection_stage="CRATE_COUNT",
          tube_index=0, inspected_at=datetime(2031, 4, 10, 0, 0, 1, tzinfo=timezone.utc).isoformat())
    by_cam = client.get("/inspection", headers=auth("op1"),
                        params={"lot": "F1", "cam_id": "PI-CAM2"}).json()
    assert [r["cam_id"] for r in by_cam] == ["PI-CAM2"]
    by_stage = client.get("/inspection", headers=auth("op1"),
                          params={"lot": "F1", "stage": "cut_length"}).json()
    assert [r["inspection_stage"] for r in by_stage] == ["CUT_LENGTH"]


def test_kpi_excludes_crate_count_rows_from_product_metrics(client, auth):
    """개수 행은 제품이 아니라 크레이트 1판이다. 공정불량률에 섞이면 틀어진다."""
    _item(client, auth)
    # 다른 테스트 파일이 2026-07 에 행을 남긴다(공유 DB) — 먼 달을 쓴다.
    period = "2031-05"
    base = datetime(2031, 5, 11, tzinfo=timezone.utc)
    for i in range(4):   # 제품 4개: OK 3, NG 1 → 250,000 ppm
        _post(client, lot=f"K{i}", inspection_stage="CUT_LENGTH",
              final_verdict="NG" if i == 0 else "OK",
              defect_codes=["LEN"] if i == 0 else [],
              inspected_at=(base + timedelta(seconds=i)).isoformat())
    # 크레이트 1판 개수 불일치(NG+COUNT) — 제품 불량이 아니다.
    _post(client, lot="K-CRATE", cam_id="PI-CAM2", inspection_stage="CRATE_COUNT",
          final_verdict="NG", defect_codes=["COUNT"], review_flag=True,
          inspected_at=(base + timedelta(seconds=10)).isoformat())
    s = client.get("/kpi/summary", headers=auth("op1"), params={"period": period}).json()
    assert s["total_inspected"] == 4, "개수 행이 제품 수에 섞였다"
    assert s["process_defect_ppm"] == pytest.approx(250_000.0)
    # 저장·연계율은 적재된 전체(5건) 기준.
    assert s["storage_mes_rate_pct"] == pytest.approx(100.0)
