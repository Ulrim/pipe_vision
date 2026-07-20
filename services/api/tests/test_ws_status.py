"""POST /inspection/status → WS /ws/live status 브로드캐스트 (CLAUDE.md §5 M6, §7.4).

워커 라이브니스 하트비트 경로 검증:
- 유효 토큰으로 WS 연결 후 POST /inspection/status 호출 → `{"event":"status", "data":{...}}`
  수신. detected=0/취득 오류 같은 "검사결과 0건" 상황도 실시간 전달됨을 확인한다.
- 하트비트는 검사결과가 아니므로 DB(inspection)에는 남지 않아야 한다.
test_ws_auth.py 의 TestClient websocket 패턴을 따른다.
"""
from __future__ import annotations

from datetime import datetime, timezone


def _make_item(client, auth):
    client.post(
        "/master/items",
        headers=auth("qa1"),
        json={
            "item_code": "HP12",
            "item_name": "Header Pipe 12",
            "ref_length_mm": 250.0,
            "tol_plus_mm": 0.5,
            "tol_minus_mm": 0.5,
            "px_to_mm_scale": 0.05,
        },
    )


def _raw_token(client, username: str) -> str:
    r = client.post(
        "/auth/login", json={"username": username, "password": "pw12345"}
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _status(**over):
    base = {
        "cam_id": "CAMWS",
        "item_code": "HP12",
        "expected": 20,
        "detected": 0,
        "ng": 0,
        "mismatch": True,
        "proc_time_ms": 42,
        "ts": datetime(2026, 6, 10, 9, 0, tzinfo=timezone.utc).isoformat(),
        "error": "acquisition_timeout",
    }
    base.update(over)
    return base


def test_status_heartbeat_broadcast(client, auth):
    """검출 0건/취득 오류 하트비트가 WS 로 브로드캐스트된다(검사결과 0건이어도)."""
    _make_item(client, auth)
    token = _raw_token(client, "op1")

    with client.websocket_connect(f"/ws/live?token={token}") as ws:
        r = client.post("/inspection/status", json=_status())
        assert r.status_code == 202, r.text
        assert r.json() == {"status": "broadcast"}

        msg = ws.receive_json()
        assert msg["event"] == "status"
        data = msg["data"]
        assert data["detected"] == 0
        assert data["expected"] == 20
        assert data["error"] == "acquisition_timeout"
        assert data["mismatch"] is True
        assert data["cam_id"] == "CAMWS"


def test_status_defaults_and_not_persisted(client, auth):
    """선택 필드 기본값 적용 + 하트비트는 DB(inspection)에 남지 않음."""
    _make_item(client, auth)
    token = _raw_token(client, "op1")

    before = client.get("/inspection", headers=auth("op1"), params={"lot": "HEARTBEAT"})
    assert before.status_code == 200, before.text

    with client.websocket_connect(f"/ws/live?token={token}") as ws:
        # 필수 필드만 전송 → ng/mismatch/proc_time_ms/error 기본값 확인.
        body = {
            "cam_id": "CAMWS",
            "item_code": "HP12",
            "expected": 1,
            "detected": 1,
            "ts": datetime(2026, 6, 10, 9, 1, tzinfo=timezone.utc).isoformat(),
        }
        r = client.post("/inspection/status", json=body)
        assert r.status_code == 202, r.text

        data = ws.receive_json()["data"]
        assert data["ng"] == 0
        assert data["mismatch"] is False
        assert data["proc_time_ms"] == 0
        assert data["error"] is None

    # 하트비트는 검사결과가 아니므로 조회 목록 건수가 늘지 않아야 한다.
    after = client.get("/inspection", headers=auth("op1"))
    assert after.status_code == 200, after.text
    assert all(row.get("lot") != "HEARTBEAT" for row in after.json())
