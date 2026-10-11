"""라벨링 빈틈 보완(2026-10-11) — 수집용 LOT 제외 + 다발 튜브 위치.

1. 수집용 LOT(TRAIN-…): 결함 시료를 일부러 섞어 찍는 학습 데이터 수집이 그 달
   KPI·통계를 망가뜨리고 MES 로 넘어가던 문제. LOT 이름으로 구분해 뺀다.
2. 다발 튜브 위치(tube_bbox): 라벨링 화면이 "이 사진의 몇 번째 튜브" 인지 보여주고,
   데이터셋 빌드가 그 튜브만 잘라 넣으려면 위치가 DB 에 있어야 한다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core import heartbeat
from core.training import is_training_lot, not_training
from db.base import SessionLocal
from db.models import Inspection, MesQualityIf

_ITEM = "HPTRAIN"


@pytest.fixture(autouse=True)
def _reset_beats():
    heartbeat.reset()
    yield
    heartbeat.reset()


def _item(client, auth):
    r = client.post("/master/items", headers=auth("qa1"), json={
        "item_code": _ITEM, "item_name": "train", "ref_length_mm": 250.0,
        "tol_plus_mm": 0.1, "tol_minus_mm": 0.1, "px_to_mm_scale": 0.1,
        "expected_count": 3,
    })
    assert r.status_code in (201, 409), r.text


def _post(client, at: datetime, **over) -> dict:
    body = {
        "lot": "PROD-1", "item_code": _ITEM, "cam_id": "TR-CAM1",
        "inspected_at": at.isoformat(), "final_verdict": "OK", "defect_codes": [],
        "review_flag": False, "mes_synced": False, "proc_time_ms": 90, "tube_index": 0,
    }
    body.update(over)
    r = client.post("/inspection", json=body)
    assert r.status_code == 201, r.text
    return r.json()


# ---- 규칙 ---------------------------------------------------------------------

def test_prefix_rule_case_insensitive_and_configurable(monkeypatch):
    monkeypatch.delenv("AIVIS_TRAINING_LOT_PREFIXES", raising=False)
    assert is_training_lot("TRAIN-OIL-01")
    assert is_training_lot("train-scr")          # 대소문자 무시
    assert not is_training_lot("L-TRAIN-1")      # 접두어만 본다
    assert not is_training_lot(None) and not is_training_lot("")

    monkeypatch.setenv("AIVIS_TRAINING_LOT_PREFIXES", "SAMPLE_, 수집")
    assert is_training_lot("sample_01") and is_training_lot("수집-0901")
    assert not is_training_lot("TRAIN-OIL-01")

    monkeypatch.setenv("AIVIS_TRAINING_LOT_PREFIXES", "")
    assert not is_training_lot("TRAIN-OIL-01"), "빈 값이면 구분을 끈다"


def test_sql_filter_treats_underscore_literally(monkeypatch):
    """접두어의 '_' 는 LIKE 와일드카드가 아니다 — 'SAMPLE_' 이 'SAMPLEX' 를 먹으면 안 된다."""
    monkeypatch.setenv("AIVIS_TRAINING_LOT_PREFIXES", "SAMPLE_")
    t0 = datetime(2034, 4, 1, tzinfo=timezone.utc)
    db = SessionLocal()
    try:
        for i, lot in enumerate(("SAMPLE_1", "SAMPLEX", "sample_2", "P-1")):
            db.add(Inspection(lot=lot, item_code=None, cam_id="SQLF",
                              inspected_at=t0 + timedelta(seconds=i),
                              final_verdict="OK", defect_codes=[]))
        db.commit()
        from sqlalchemy import select

        lots = sorted(db.execute(
            select(Inspection.lot).where(Inspection.cam_id == "SQLF",
                                         not_training(Inspection.lot))
        ).scalars().all())
        assert lots == ["P-1", "SAMPLEX"]
    finally:
        db.query(Inspection).filter(Inspection.cam_id == "SQLF").delete()
        db.commit()
        db.close()


# ---- KPI · 통계 · MES ----------------------------------------------------------

def test_kpi_excludes_training_lot_and_reports_count(client, auth):
    _item(client, auth)
    t0 = datetime(2034, 5, 3, 1, 0, tzinfo=timezone.utc)
    _post(client, t0, lot="PROD-1")
    _post(client, t0 + timedelta(seconds=1), lot="PROD-1", final_verdict="NG",
          defect_codes=["LEN"])
    for i in range(3):  # 결함 시료를 일부러 찍었다 — 전부 NG
        _post(client, t0 + timedelta(seconds=10 + i), lot="TRAIN-OIL-01",
              final_verdict="NG", defect_codes=["OIL"], inspection_stage="POST_WASH_SURFACE")

    r = client.get("/kpi/summary", headers=auth("qa1"), params={"period": "2034-05"})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["total_inspected"] == 2 and s["defect_count"] == 1
    assert s["process_defect_ppm"] == pytest.approx(500_000.0)
    assert s["stored_count"] == 2, "연계율 분모에서도 뺀다"
    assert s["excluded_training_count"] == 3


def test_stats_exclude_training_by_default(client, auth):
    _item(client, auth)
    t0 = datetime(2034, 6, 3, 1, 0, tzinfo=timezone.utc)
    _post(client, t0, lot="PROD-6")
    _post(client, t0 + timedelta(seconds=1), lot="TRAIN-DIS-01", final_verdict="NG",
          defect_codes=["DIS"])
    params = {"item": _ITEM, "from": "2034-06-01T00:00:00Z", "to": "2034-07-01T00:00:00Z"}
    s = client.get("/inspection/stats", headers=auth("op1"), params=params).json()
    assert (s["total"], s["ng"]) == (1, 0)
    s2 = client.get("/inspection/stats", headers=auth("op1"),
                    params={**params, "include_training": "true"}).json()
    assert (s2["total"], s2["ng"]) == (2, 1)


def test_training_rows_are_not_staged_for_mes(client, auth):
    """table 모드: 수집용 행은 스테이징 테이블(MES 가 가져가는 곳)에 들어가지 않는다."""
    _item(client, auth)
    t0 = datetime(2034, 7, 3, 1, 0, tzinfo=timezone.utc)
    prod = _post(client, t0, lot="PROD-7")
    train = _post(client, t0 + timedelta(seconds=1), lot="TRAIN-SCR-01")
    db = SessionLocal()
    try:
        staged = {r.inspection_id for r in db.query(MesQualityIf).all()}
        assert prod["id"] in staged
        assert train["id"] not in staged
    finally:
        db.close()


# ---- 화면 경고 -----------------------------------------------------------------

def test_heartbeat_and_station_card_flag_training(client, auth):
    r = client.post("/inspection/status", json={
        "cam_id": "TR-HB", "item_code": _ITEM, "expected": 3, "detected": 3,
        "ts": datetime.now(timezone.utc).isoformat(), "ng": 0, "mismatch": False,
        "proc_time_ms": 100, "stage": "POST_WASH_SURFACE", "error": None,
        "lot": "TRAIN-OIL-02",
    })
    assert r.status_code == 202, r.text
    b = heartbeat.get("TR-HB")
    assert b.cycle["lot"] == "TRAIN-OIL-02" and b.cycle["training"] is True

    st = {s["cam_id"]: s for s in client.get(
        "/system/stations", headers=auth("op1")).json()["stations"]}
    assert st["TR-HB"]["training"] is True
    assert st["TR-HB"]["lot"] == "TRAIN-OIL-02"


def test_active_order_flags_training(client, auth):
    _item(client, auth)
    try:
        r = client.put("/master/active", headers=auth("qa1"),
                       json={"item_code": _ITEM, "lot": "TRAIN-OIL-03", "work_order": None})
        assert r.status_code == 200, r.text
        assert r.json()["training"] is True
        r = client.put("/master/active", headers=auth("qa1"),
                       json={"item_code": _ITEM, "lot": "PROD-9", "work_order": None})
        assert r.json()["training"] is False
    finally:
        client.delete("/master/active", headers=auth("qa1"))


# ---- 다발 튜브 위치 ------------------------------------------------------------

def test_tube_bbox_roundtrip_and_label_queue_fields(client, auth):
    """다발 3개 사진: 큐는 튜브 번호·사진의 튜브 수·위치를 주고, 같은 사진끼리 붙여 준다."""
    _item(client, auth)
    at = datetime(2034, 8, 3, 1, 0, tzinfo=timezone.utc)
    ids = []
    for i in range(3):
        ids.append(_post(
            client, at, lot="TRAIN-BBOX", cam_id="TR-BOX", tube_index=i,
            tube_bbox=[0, 20 * i, 200, 20 * i + 18],
            result_image_path=f"result/bbox_{i}.jpg", raw_image_path="raw/bbox.jpg",
            final_verdict="NG" if i == 1 else "OK",
            defect_codes=["OIL"] if i == 1 else [],
        )["id"])
    got = client.get(f"/inspection/{ids[2]}", headers=auth("op1")).json()
    assert got["tube_bbox"] == [0, 40, 200, 58]

    q = client.get("/labels/queue", headers=auth("op1"), params={"limit": 200}).json()
    mine = [x for x in q if x["inspection_id"] in ids]
    assert [x["tube_index"] for x in mine] == [0, 1, 2], "같은 사진의 튜브는 연달아"
    assert all(x["frame_total"] == 3 and x["training"] for x in mine)
    assert mine[1]["tube_bbox"] == [0, 20, 200, 38]
    assert mine[0]["cam_id"] == "TR-BOX"

    # 라벨을 붙이고 내보내면 데이터셋 빌드가 쓸 위치·구도가 함께 나온다.
    r = client.put(f"/labels/{ids[1]}", headers=auth("qa1"),
                   json={"labels": ["OIL"], "border": False})
    assert r.status_code == 200, r.text
    exp = client.get("/labels/export", headers=auth("qa1")).json()
    row = next(x for x in exp["items"] if x["inspection_id"] == ids[1])
    assert row["tube_bbox"] == [0, 20, 200, 38]
    assert row["tube_index"] == 1 and row["frame_total"] == 3
    assert row["view"] == "SIDE" and row["training"] is True
    assert exp["count"] == len(exp["items"])
