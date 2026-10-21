"""한 도메인 아래 `/mes-api` 로 물린 배포(Vercel services, 2026-10-21) — 접두어 붙은 요청과
안 붙은 요청(AIVIS API 가 바인딩 주소로 직접) 모두 같은 앱에서 돈다."""
from __future__ import annotations

from fastapi.testclient import TestClient

from mes_app.main import app
from mes_app.prefix import StripPathPrefix, path_prefix_from_env


def test_mes_answers_with_and_without_prefix():
    c = TestClient(StripPathPrefix(app, prefix="/mes-api"))
    assert c.get("/mes-api/health").json()["service"] == "mes"
    assert c.get("/health").json()["service"] == "mes"


def test_mes_prefix_from_env():
    assert path_prefix_from_env({}) == ""
    assert path_prefix_from_env({"VERCEL": "1"}) == "/mes-api"
    assert path_prefix_from_env({"MES_PATH_PREFIX": "/m"}) == "/m"
