"""WS /ws/live JWT 인증 (CLAUDE.md §5 M14, §7.4).

사내 도구이므로 `/ws/live?token=<JWT>` 쿼리 JWT 를 요구한다.
검증:
- 유효 토큰: 연결 성공 + POST /inspection 브로드캐스트 이벤트 수신.
- 토큰 누락/무효: accept 전 1008(policy violation) close.
FastAPI TestClient websocket 사용.
"""
from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timezone

import pytest
import uvicorn
import websockets
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


# --- 프로토콜 레벨(실 소켓) 회귀 테스트 -------------------------------------
#
# 위 test_ws_missing/invalid_token_rejected_1008 는 FastAPI TestClient(ASGI
# in-process 트랜스포트)로 검증하는데, TestClient.websocket_connect() 는
# accept() 전 close() 든 accept() 후 close() 든 WebSocketDisconnect(code=1008)
# 로 동일하게 관측된다 — 즉 "accept 전 close 가 실제로는 HTTP 403 이 되어
# 브라우저에 code=1008 이 전달되지 않는다"는 프로토콜 레벨 버그를 TestClient
# 로는 절대 잡을 수 없다(실측: 버그가 있던 구버전 코드도 이 파일의 TestClient
# 테스트 3개를 전부 통과했다). 실제 소켓 + websockets 클라이언트로 재검증한다.
_LIVE_HOST = "127.0.0.1"
_LIVE_PORT = 18765


@pytest.fixture(scope="module")
def live_server():
    """실제 uvicorn(ASGI, 실 TCP 소켓) 서버를 백그라운드 스레드로 기동."""
    from main import app

    config = uvicorn.Config(app, host=_LIVE_HOST, port=_LIVE_PORT, log_level="error")
    server = uvicorn.Server(config)

    def _run() -> None:
        asyncio.run(server.serve())

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    for _ in range(50):
        if getattr(server, "started", False):
            break
        time.sleep(0.1)
    else:
        raise RuntimeError("live uvicorn 서버가 시간 내 기동하지 못함")
    yield f"ws://{_LIVE_HOST}:{_LIVE_PORT}"
    server.should_exit = True
    thread.join(timeout=5)


def _connect_and_get_close_code(uri: str) -> int:
    async def _run() -> int:
        ws = await websockets.connect(uri, open_timeout=5)
        with pytest.raises(websockets.exceptions.ConnectionClosed):
            await ws.recv()
        return ws.close_code

    return asyncio.run(_run())


def test_ws_live_socket_missing_token_delivers_close_code_1008(live_server):
    """실 소켓: 토큰 누락 시 클라이언트가 정확히 close_code==1008 을 수신해야
    한다. (accept() 없이 close() 만 하면 브라우저는 이를 1006(비정상 종료)으로
    보고해 HMI 의 "인증만료→로그인 복귀" 로직이 발동하지 않고 "재연결 중…"
    이 무한 반복되는 실제 장애로 이어진다 — 이 테스트가 그 회귀를 잡는다.)
    """
    code = _connect_and_get_close_code(f"{live_server}/ws/live")
    assert code == 1008


def test_ws_live_socket_invalid_token_delivers_close_code_1008(live_server):
    """실 소켓: 무효 토큰도 동일하게 close_code==1008 이어야 한다."""
    code = _connect_and_get_close_code(f"{live_server}/ws/live?token=not-a-real-jwt")
    assert code == 1008


def test_ws_live_socket_valid_token_connects(client, live_server):
    """실 소켓: 유효 토큰이면 오프닝 핸드셰이크가 정상 완료되어야 한다."""
    token = _raw_token(client, "op1")

    async def _run() -> bool:
        async with websockets.connect(
            f"{live_server}/ws/live?token={token}", open_timeout=5
        ) as ws:
            return ws.close_code is None  # 아직 열려 있음(정상)

    assert asyncio.run(_run()) is True
