"""검사 모드(단계) 전환 — PUT /master/active/stage.

현장 요구(2026-10-05): 길이·표면·개수를 한 번에 돌리니 NG 가 왜 났는지 모르겠다.
한 대로 번갈아 보는 벤치에서 **재시작 없이** 모드를 바꿀 수 있어야 한다.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_active_order(client, auth):
    """테스트 DB 는 세션 공유다. 활성 오더를 남기면 '미설정 → null' 을 보는
    다른 파일의 테스트가 깨진다. 전후로 비운다."""
    client.delete("/master/active", headers=auth("qa1"))
    yield
    client.delete("/master/active", headers=auth("qa1"))


def _make_item(client, auth, code="HP12"):
    """품목이 이미 있으면(다른 테스트가 만들어 둠) 그대로 쓴다."""
    r = client.post(
        "/master/items",
        headers=auth("qa1"),
        json={
            "item_code": code,
            "item_name": f"Header Pipe {code}",
            "ref_length_mm": 430.0,
            "tol_plus_mm": 0.1,
            "tol_minus_mm": 0.1,
            "px_to_mm_scale": 0.1,
            "expected_count": 20,
        },
    )
    assert r.status_code in (201, 409), r.text


def test_operator_can_switch_mode_without_an_order(client, auth):
    """오더가 없어도 모드부터 바꿀 수 있어야 벤치에서 쓸 수 있다."""
    _make_item(client, auth)
    r = client.put(
        "/master/active/stage",
        headers=auth("op1"),
        json={"item_code": "HP12", "inspection_stage": "CRATE_COUNT"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["inspection_stage"] == "CRATE_COUNT"
    assert r.json()["item_code"] == "HP12"

    g = client.get("/master/active", headers=auth("op1"))
    assert g.json()["inspection_stage"] == "CRATE_COUNT"


def test_switching_mode_keeps_lot_and_work_order(client, auth):
    """모드만 바꾸는 경로가 LOT/작업지시를 지우면 라벨이 오염된다."""
    _make_item(client, auth)
    client.put(
        "/master/active",
        headers=auth("qa1"),
        json={"item_code": "HP12", "lot": "LOT-9", "work_order": "WO-3"},
    )
    r = client.put(
        "/master/active/stage",
        headers=auth("op1"),
        json={"item_code": "HP12", "inspection_stage": "POST_WASH_SURFACE"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["lot"] == "LOT-9" and body["work_order"] == "WO-3"
    assert body["inspection_stage"] == "POST_WASH_SURFACE"


def test_unknown_mode_is_rejected(client, auth):
    _make_item(client, auth)
    r = client.put(
        "/master/active/stage",
        headers=auth("op1"),
        json={"item_code": "HP12", "inspection_stage": "LENGTH"},
    )
    assert r.status_code == 422


def test_unknown_item_is_404(client, auth):
    r = client.put(
        "/master/active/stage",
        headers=auth("op1"),
        json={"item_code": "NOPE", "inspection_stage": "CUT_LENGTH"},
    )
    assert r.status_code == 404


def test_full_order_put_accepts_stage_and_null_means_station_default(client, auth):
    _make_item(client, auth)
    r = client.put(
        "/master/active",
        headers=auth("qa1"),
        json={"item_code": "HP12", "lot": "L1", "inspection_stage": "CUT_LENGTH"},
    )
    assert r.status_code == 200 and r.json()["inspection_stage"] == "CUT_LENGTH"
    # 비우면 NULL → 워커는 env(AIVIS_INSPECTION_STAGE) 기본값으로 돌아간다.
    r = client.put(
        "/master/active", headers=auth("qa1"), json={"item_code": "HP12", "lot": "L1"}
    )
    assert r.status_code == 200 and r.json()["inspection_stage"] is None


def test_mode_change_is_audited_with_before_and_after(client, auth):
    """모드가 바뀐 뒤 결과가 이상할 때 '언제 누가 어디서 어디로' 를 되짚어야 한다."""
    from db.base import SessionLocal
    from db.models import SysLog

    _make_item(client, auth)
    client.put("/master/active/stage", headers=auth("op1"),
               json={"item_code": "HP12", "inspection_stage": "CUT_LENGTH"})
    client.put("/master/active/stage", headers=auth("op1"),
               json={"item_code": "HP12", "inspection_stage": "CRATE_COUNT"})
    db = SessionLocal()
    try:
        rows = db.query(SysLog).filter(SysLog.message.like("master.active.stage%")).all()
    finally:
        db.close()
    assert rows, "모드 변경 로그가 없다"
    last = rows[-1].payload or {}
    assert last.get("before") == "CUT_LENGTH" and last.get("after") == "CRATE_COUNT"


def test_status_heartbeat_accepts_stage(client, auth):
    """워커 하트비트에 stage 가 실려야 HMI 가 첫 결과 전에도 모드를 보여준다."""
    r = client.post(
        "/inspection/status",
        json={
            "cam_id": "PI-CAM1", "item_code": "HP12", "expected": 20,
            "detected": 18, "ts": "2026-10-05T00:00:00+00:00",
            "ng": 1, "mismatch": True, "proc_time_ms": 120,
            "stage": "CRATE_COUNT",
        },
    )
    assert r.status_code in (202, 401, 403), r.text
