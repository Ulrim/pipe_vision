"""검사 단계(inspection_stage) 적재·조회 (데이터 정의서 3-3/4-3/5-3 필수 항목).

공정상 두 지점에서 서로 다른 것을 본다. 촬영 조건이 정반대라(길이=백라이트,
표면=확산광/사광) 한 스테이션이 겸할 수 없고, 같은 제품이라도 어느 단계에서
찍혔는지에 따라 판정 근거와 학습 분포가 달라진다. 그래서 검사 1건마다 실어
나른다.
"""
from __future__ import annotations

from datetime import datetime, timezone


def _post(client, **over):
    base = {
        "lot": "STG",
        "item_code": "STG",
        "cam_id": "C1",
        # KPI 테스트가 쓰지 않는 월 — 같은 DB 를 공유하므로 월 집계에 끼어들면 안 된다.
        "inspected_at": datetime(2027, 2, 1, 9, 0, tzinfo=timezone.utc).isoformat(),
        "final_verdict": "OK",
        "defect_codes": [],
        "proc_time_ms": 100,
    }
    base.update(over)
    return client.post("/inspection", json=base)


def _ensure_item(client, auth):
    client.post("/master/items", headers=auth("qa1"), json={
        "item_code": "STG", "item_name": "stage", "ref_length_mm": 100.0,
        "tol_plus_mm": 0.5, "tol_minus_mm": 0.5, "px_to_mm_scale": 0.05,
    })


def test_stage_round_trips(client, auth):
    """워커가 실은 단계가 그대로 저장되고 조회에 나온다."""
    _ensure_item(client, auth)
    r = _post(client, cam_id="S1", inspection_stage="POST_WASH_SURFACE")
    assert r.status_code == 201, r.text
    iid = r.json()["id"]

    got = client.get(f"/inspection/{iid}", headers=auth("op1")).json()
    assert got["inspection_stage"] == "POST_WASH_SURFACE"


def test_stage_optional_for_legacy_rows(client, auth):
    """단계 없이 적재해도 거부하지 않는다 — 단계 구분 이전 장비도 계속 돈다."""
    _ensure_item(client, auth)
    r = _post(client, cam_id="S2")
    assert r.status_code == 201, r.text
    assert client.get(f"/inspection/{r.json()['id']}", headers=auth("op1")).json()[
        "inspection_stage"
    ] is None


def test_unknown_stage_rejected(client, auth):
    """오타는 거부한다. 통과시키면 그 스테이션 데이터 전체가 잘못된 단계로 쌓이고,
    단계별 정확도를 집계할 때야 드러난다 — 그때는 되돌릴 수 없다."""
    _ensure_item(client, auth)
    r = _post(client, cam_id="S3", inspection_stage="CUT")
    assert r.status_code == 422
