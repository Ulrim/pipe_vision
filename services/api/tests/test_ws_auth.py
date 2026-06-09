"""WS /ws/live JWT 인증 (CLAUDE.md §5 M14, §7.4).

사내 도구이므로 `/ws/live?token=<JWT>` 쿼리 JWT 를 요구한다.
검증:
- 유효 토큰: 연결 성공 + POST /inspection 브로드캐스트 이벤트 수신.
- 토큰 누락/무효: accept 전 1008(policy violation) close.
FastAPI TestClient websocket 사용.
"""
from __future__ import annotations

from datetime import datetime, timezone

from starlette.websockets import WebSocketDisconnect


def _raw_token(client, username: str) -> str:
    r = client.post(
        "/auth/login", json={"username": username, "password": "pw12345"}
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


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


def _insp(**over):
    base = {
        "lot": "LOTWS",
        "item_code": "HP12",
        "cam_id": "CAMWS",
        "inspected_at": datetime(2026, 6, 10, 9, 0, tzinfo=timezone.utc).isoformat(),
        "final_verdict": "OK",
        "defect_codes": [],
        "review_flag": False,
        "mes_synced": False,
        "proc_time_ms": 110,
    }
    base.update(over)
    return base


def test_ws_valid_token_connects_and_receives(client, auth):
    """유효 토큰으로 연결 후, POST /inspection 결과를 브로드캐스트로 수신."""
    _make_item(client, auth)
    token = _raw_token(client, "op1")

    with client.websocket_connect(f"/ws/live?token={token}") as ws:
        r = client.post("/inspection", json=_insp(lot="LOTWSOK"))
        assert r.status_code == 201, r.text
        msg = ws.receive_json()
        assert msg["event"] == "inspection"
        assert msg["data"]["lot"] == "LOTWSOK"


def test_ws_missing_token_rejected_1008(client):
    """토큰 누락: accept 전 1008 close."""
    with pytest_raises_ws_close(1008):
        with client.websocket_connect("/ws/live") as ws:
            ws.receive_text()


def test_ws_invalid_token_rejected_1008(client):
    """무효 토큰: accept 전 1008 close."""
    with pytest_raises_ws_close(1008):
        with client.websocket_connect("/ws/live?token=not-a-real-jwt") as ws:
            ws.receive_text()


class pytest_raises_ws_close:
    """WebSocketDisconnect(code) 를 기대하는 컨텍스트 매니저."""

    def __init__(self, code: int) -> None:
        self.code = code

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        assert exc_type is WebSocketDisconnect, f"기대: 연결 거절, 실제: {exc_type}"
        assert exc.code == self.code, f"기대 code {self.code}, 실제 {exc.code}"
        return True
