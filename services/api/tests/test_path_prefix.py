"""한 도메인 아래 `/api` 로 물린 배포(Vercel services, 2026-10-21).

Vercel 은 `/api/(.*)` 를 이 서비스로 보낼 때 경로를 그대로 넘긴다. 접두어가 붙은 요청과
안 붙은 요청(파이 워커가 직접, 현장 단독·Render)이 같은 앱에서 모두 돌아야 한다.
MES 수신 주소는 `MES_REST_URL` 이 우선, 없으면 서비스 바인딩 `MES_SERVICE_URL` 에서 만든다.
"""
from __future__ import annotations

from fastapi import FastAPI, Request, WebSocket
from fastapi.testclient import TestClient

from core.prefix import StripPathPrefix, path_prefix_from_env
from mes.config import resolve_rest_url


def _app(prefix: str) -> FastAPI:
    app = FastAPI()

    @app.get("/inspection")
    def insp(request: Request):
        return {"path": request.url.path, "root_path": request.scope.get("root_path", "")}

    @app.websocket("/ws/live")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text("hi")
        await websocket.close()

    app.add_middleware(StripPathPrefix, prefix=prefix)
    return app


def test_prefixed_and_plain_paths_both_route():
    c = TestClient(_app("/api"))
    r = c.get("/api/inspection")
    assert r.status_code == 200 and r.json()["root_path"] == "/api"
    assert c.get("/inspection").status_code == 200, "접두어 없이 온 요청도 그대로"
    assert c.get("/apix/inspection").status_code == 404, "비슷한 이름은 벗기지 않는다"
    with c.websocket_connect("/api/ws/live") as ws:
        assert ws.receive_text() == "hi"


def test_no_prefix_is_a_no_op():
    c = TestClient(_app(""))
    assert c.get("/inspection").status_code == 200
    assert c.get("/api/inspection").status_code == 404


def test_prefix_from_env():
    assert path_prefix_from_env({}) == ""
    assert path_prefix_from_env({"VERCEL": "1"}) == "/api"
    assert path_prefix_from_env({"VERCEL": "1", "AIVIS_PATH_PREFIX": "aivis/"}) == "/aivis"
    assert path_prefix_from_env({"AIVIS_PATH_PREFIX": "/api"}) == "/api"


def test_real_app_answers_under_prefix(client):
    # 시험 클라이언트의 앱은 접두어 없이 떠 있다 — 접두어 미들웨어를 감싸 같은 앱으로 확인.
    wrapped = TestClient(StripPathPrefix(client.app, prefix="/api"))
    assert wrapped.get("/api/health").json()["status"] in ("ok", "degraded")
    assert wrapped.get("/health").status_code == 200


def test_mes_rest_url_from_binding():
    assert resolve_rest_url({}) is None
    assert resolve_rest_url({"MES_SERVICE_URL": "http://mes.internal:3000/"}) == \
        "http://mes.internal:3000/intake/quality"
    assert resolve_rest_url({"MES_REST_URL": "https://x/in", "MES_SERVICE_URL": "http://mes"}) == "https://x/in"
