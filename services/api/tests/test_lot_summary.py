"""LOT 단위 통합 판정 — GET /inspection/lot-summary (2026-10-10 점검 보완).

점검: "모드 분리 후 길이+표면 통합판정이 안 일어남". 다발 튜브는 스테이션 간 개별
추적이 안 되므로 LOT 단위로 합친다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture(autouse=True)
def _clean(client, auth):
    for cam in ("LS-1", "LS-2", "LS-3"):
        client.delete(f"/master/stations/{cam}", headers=auth("op1"))
    client.delete("/master/active", headers=auth("qa1"))
    yield
    for cam in ("LS-1", "LS-2", "LS-3"):
        client.delete(f"/master/stations/{cam}", headers=auth("op1"))
    client.delete("/master/active", headers=auth("qa1"))


def _item(client, auth):
    client.post("/master/items", headers=auth("qa1"), json={
        "item_code": "LSI", "item_name": "l", "ref_length_mm": 430.0,
        "tol_plus_mm": 0.1, "tol_minus_mm": 0.1, "px_to_mm_scale": 0.1,
    })


_t = [datetime(2031, 6, 1, tzinfo=timezone.utc)]


def _post(client, lot, stage, cam, verdict="OK", codes=(), review=False, idx=0):
    _t[0] += timedelta(seconds=1)
    r = client.post("/inspection", json={
        "lot": lot, "item_code": "LSI", "cam_id": cam, "inspection_stage": stage,
        "inspected_at": _t[0].isoformat(), "final_verdict": verdict,
        "defect_codes": list(codes), "review_flag": review, "mes_synced": False,
        "proc_time_ms": 100, "tube_index": idx,
    })
    assert r.status_code == 201, r.text


def _sum(client, auth, lot, **params):
    r = client.get("/inspection/lot-summary", headers=auth("op1"), params={"lot": lot, **params})
    assert r.status_code == 200, r.text
    return r.json()


def test_any_stage_ng_makes_lot_ng_with_numeric_reasons(client, auth):
    _item(client, auth)
    for i in range(3):
        _post(client, "LS-A", "CUT_LENGTH", "LS-1", idx=i)
    _post(client, "LS-A", "CUT_LENGTH", "LS-1", "NG", ["LEN"], review=True, idx=3)
    _post(client, "LS-A", "CRATE_COUNT", "LS-2")
    s = _sum(client, auth, "LS-A", require="CUT_LENGTH,CRATE_COUNT")
    assert s["final_verdict"] == "NG"
    assert "길이 검사 NG 1개 / 4개 (LEN 1)" in s["reasons"]
    assert "길이 검사 재확인 대기 1건" in s["reasons"]
    st = {x["stage"]: x for x in s["stages"]}
    assert st["CUT_LENGTH"]["ng_rate_pct"] == 25.0 and st["CRATE_COUNT"]["ng"] == 0
    assert [x["stage"] for x in s["stages"]] == ["CUT_LENGTH", "CRATE_COUNT"], "공정 순서"


def test_missing_required_stage_is_incomplete(client, auth):
    _item(client, auth)
    _post(client, "LS-B", "CUT_LENGTH", "LS-1")
    s = _sum(client, auth, "LS-B", require="CUT_LENGTH,POST_WASH_SURFACE")
    assert s["final_verdict"] == "INCOMPLETE"
    assert s["missing_stages"] == ["POST_WASH_SURFACE"]
    assert "표면 검사 결과 없음" in s["reasons"]


def test_required_defaults_to_configured_stations(client, auth):
    _item(client, auth)
    client.put("/master/active/stage", headers=auth("op1"),
               json={"item_code": "LSI", "inspection_stage": "CUT_LENGTH", "cam_id": "LS-1"})
    client.put("/master/active/stage", headers=auth("op1"),
               json={"item_code": "LSI", "inspection_stage": "CRATE_COUNT", "cam_id": "LS-2"})
    _post(client, "LS-C", "CUT_LENGTH", "LS-1")
    s = _sum(client, auth, "LS-C")
    assert s["required_stages"] == ["CRATE_COUNT", "CUT_LENGTH"]
    assert s["final_verdict"] == "INCOMPLETE"
    _post(client, "LS-C", "CRATE_COUNT", "LS-2")
    assert _sum(client, auth, "LS-C")["final_verdict"] == "OK"


def test_unknown_lot_is_none(client, auth):
    assert _sum(client, auth, "NO-SUCH-LOT")["final_verdict"] == "NONE"
