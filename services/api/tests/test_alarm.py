"""연속 NG 알람 검증 (M6). cam_id 단위 연속 NG 임계 도달 시 alarm 발행, OK 리셋."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest


@pytest.fixture
def captured(client, auth, monkeypatch):
    """hub.broadcast 를 가로채 발행된 WS 이벤트를 수집한다."""
    import ws.hub as hub_mod
    from ws.alarm import tracker

    # FK 충족용 기준정보 시드(멱등).
    client.post("/master/items", headers=auth("qa1"), json={
        "item_code": "ALM", "item_name": "alarm", "ref_length_mm": 100.0,
        "tol_plus_mm": 0.5, "tol_minus_mm": 0.5, "px_to_mm_scale": 0.05,
    })

    events: list[dict] = []

    async def _capture(message):
        events.append(message)

    # inspection 라우터가 import 한 hub 인스턴스의 broadcast 를 교체.
    monkeypatch.setattr(hub_mod.hub, "broadcast", _capture)
    tracker.reset()  # cam 카운터 초기화
    yield events
    tracker.reset()


def _post(client, cam_id, verdict, lot, defects=None):
    body = {
        "lot": lot, "item_code": "ALM", "cam_id": cam_id,
        "inspected_at": datetime(2026, 2, 1, 9, 0, tzinfo=timezone.utc).isoformat(),
        "final_verdict": verdict, "defect_codes": defects or ([] if verdict == "OK" else ["LEN"]),
        "review_flag": False, "mes_synced": False, "proc_time_ms": 100,
    }
    r = client.post("/inspection", json=body)
    assert r.status_code == 201, r.text


def _consec_events(events):
    return [
        e for e in events
        if e.get("event") == "alarm"
        and e.get("data", {}).get("kind") == "consecutive_ng"
    ]


def test_consecutive_ng_triggers_alarm_at_threshold(client, captured):
    """기본 임계 3. 3연속 NG 째에 consecutive_ng 알람 발행."""
    _post(client, "LINE1", "NG", "A1")
    assert _consec_events(captured) == []  # 1연속: 아직
    _post(client, "LINE1", "NG", "A2")
    assert _consec_events(captured) == []  # 2연속: 아직
    _post(client, "LINE1", "NG", "A3")
    fired = _consec_events(captured)
    assert len(fired) == 1
    data = fired[0]["data"]
    assert data["kind"] == "consecutive_ng"
    assert data["cam_id"] == "LINE1"
    assert data["count"] == 3
    assert data["threshold"] == 3


def test_ok_resets_counter(client, captured):
    _post(client, "LINE2", "NG", "B1")
    _post(client, "LINE2", "NG", "B2")
    _post(client, "LINE2", "OK", "B3")  # 리셋
    _post(client, "LINE2", "NG", "B4")
    _post(client, "LINE2", "NG", "B5")
    # OK 로 리셋되어 2연속까지만 -> 알람 없음
    assert _consec_events(captured) == []
    _post(client, "LINE2", "NG", "B6")  # 이제 3연속 -> 알람
    assert len(_consec_events(captured)) == 1


def test_counter_is_per_cam(client, captured):
    """cam 별 독립 카운터: 서로 다른 라인의 NG 는 합산되지 않는다."""
    _post(client, "CAMA", "NG", "C1")
    _post(client, "CAMB", "NG", "C2")
    _post(client, "CAMA", "NG", "C3")
    _post(client, "CAMB", "NG", "C4")
    # 각 cam 2연속 -> 임계 미달
    assert _consec_events(captured) == []
    _post(client, "CAMA", "NG", "C5")  # CAMA 3연속
    fired = _consec_events(captured)
    assert len(fired) == 1
    assert fired[0]["data"]["cam_id"] == "CAMA"


def test_single_ng_alarm_still_emitted(client, captured):
    """단일 NG 알람(kind=ng)은 기존대로 매 NG 발행."""
    _post(client, "LINE3", "NG", "D1")
    ng_alarms = [
        e for e in captured
        if e.get("event") == "alarm" and e.get("data", {}).get("kind") == "ng"
    ]
    assert len(ng_alarms) == 1
    assert ng_alarms[0]["data"]["cam_id"] == "LINE3"
