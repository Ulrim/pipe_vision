"""CORS 미들웨어 (클라우드 데모 교차 출처) 단위 테스트.

- ALLOWED_ORIGINS 미설정: "*" 허용, credentials 불가.
- ALLOWED_ORIGINS 명시: 해당 출처만 허용, credentials=True.
프리플라이트(OPTIONS)와 실제 요청의 Access-Control-Allow-Origin 헤더를 검증한다.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from core.config import Settings


def _app_with_origins(raw: str | None) -> FastAPI:
    """구성된 ALLOWED_ORIGINS 로 CORS 를 설정한 격리 앱(설정 캐시 회피)."""
    s = Settings.__new__(Settings)
    s.allowed_origins = Settings._parse_origins(raw)
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.allowed_origins,
        allow_credentials=s.cors_allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/ping")
    def ping():
        return {"ok": True}

    return app


def _preflight(client: TestClient, origin: str):
    return client.options(
        "/ping",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )


def test_parse_origins():
    assert Settings._parse_origins(None) == ["*"]
    assert Settings._parse_origins("") == ["*"]
    assert Settings._parse_origins("   ") == ["*"]
    assert Settings._parse_origins(
        "https://a.vercel.app, https://b.vercel.app ,"
    ) == ["https://a.vercel.app", "https://b.vercel.app"]


def test_cors_wildcard_when_unset():
    client = TestClient(_app_with_origins(None))
    origin = "https://aivis-hmi.vercel.app"

    pre = _preflight(client, origin)
    assert pre.status_code == 200
    assert pre.headers.get("access-control-allow-origin") == "*"
    # "*" 와 credentials 동시 사용 회피: credentials 헤더 없어야 함.
    assert "access-control-allow-credentials" not in pre.headers

    real = client.get("/ping", headers={"Origin": origin})
    assert real.status_code == 200
    assert real.headers.get("access-control-allow-origin") == "*"
    assert "access-control-allow-credentials" not in real.headers


def test_cors_explicit_origins_allow_credentials():
    allowed = "https://aivis-hmi.vercel.app,https://aivis-dashboard.vercel.app"
    client = TestClient(_app_with_origins(allowed))
    origin = "https://aivis-dashboard.vercel.app"

    pre = _preflight(client, origin)
    assert pre.status_code == 200
    assert pre.headers.get("access-control-allow-origin") == origin
    assert pre.headers.get("access-control-allow-credentials") == "true"

    real = client.get("/ping", headers={"Origin": origin})
    assert real.status_code == 200
    assert real.headers.get("access-control-allow-origin") == origin
    assert real.headers.get("access-control-allow-credentials") == "true"


def test_cors_explicit_origin_not_allowed():
    allowed = "https://aivis-hmi.vercel.app"
    client = TestClient(_app_with_origins(allowed))
    # 목록에 없는 출처는 ACAO 헤더가 부여되지 않는다.
    real = client.get("/ping", headers={"Origin": "https://evil.example.com"})
    assert real.status_code == 200
    assert real.headers.get("access-control-allow-origin") != "https://evil.example.com"
