"""공개 클라우드 배포 대비 (2026-10-15).

1. 워커는 서비스 토큰으로 기준정보·현재 오더를 **읽는다** — 파이에 관리자 비밀번호를 두지 않는다.
   그 토큰으로 쓰기는 못 한다.
2. POST /mes/quality 는 서비스 토큰이 있어야 한다(종전엔 인증이 없었다).
3. Supabase 형식 주소(postgresql://)를 psycopg 드라이버 주소로 바꾼다.
4. AIVIS_REQUIRE_SERVICE_TOKEN=true 인데 토큰이 없으면 기동하지 않는다.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.config import normalize_db_url


@pytest.fixture
def token_configured(monkeypatch):
    import core.security as sec

    real = sec.get_settings()

    class _S:
        service_token = "svc-token"

        def __getattr__(self, name):
            return getattr(real, name)

    monkeypatch.setattr(sec, "get_settings", lambda: _S())


def _item(client, auth):
    client.post("/master/items", headers=auth("qa1"), json={
        "item_code": "HPSEC", "item_name": "sec", "ref_length_mm": 100.0,
        "tol_plus_mm": 0.5, "tol_minus_mm": 0.5, "px_to_mm_scale": 0.05})


def test_worker_reads_master_with_service_token(client, auth, token_configured):
    _item(client, auth)
    h = {"X-Service-Token": "svc-token"}
    r = client.get("/master/items/HPSEC", headers=h)
    assert r.status_code == 200 and r.json()["item_code"] == "HPSEC"
    assert client.get("/master/active", headers=h, params={"cam_id": "PI-CAM1"}).status_code == 200

    # 틀린 토큰·토큰 없음은 로그인을 요구한다.
    assert client.get("/master/items/HPSEC", headers={"X-Service-Token": "x"}).status_code == 401
    assert client.get("/master/items/HPSEC").status_code == 401
    # 사람 로그인은 그대로 된다.
    assert client.get("/master/items/HPSEC", headers=auth("op1")).status_code == 200


def test_service_token_cannot_write_master(client, auth, token_configured):
    _item(client, auth)
    h = {"X-Service-Token": "svc-token"}
    r = client.put("/master/active", headers=h,
                   json={"item_code": "HPSEC", "lot": "X", "work_order": None})
    assert r.status_code == 401, "읽기 전용 — 오더를 바꿀 수 없다"
    r = client.put("/master/items/HPSEC/spec", headers=h, json={"ref_length_mm": 1.0})
    assert r.status_code == 401


def test_mes_quality_requires_service_token(client, token_configured):
    body = {"lot": "SEC-L", "item_code": "HPSEC", "cam_id": "C",
            "inspected_at": datetime(2037, 1, 1, tzinfo=timezone.utc).isoformat(),
            "final_verdict": "OK", "defect_codes": []}
    assert client.post("/mes/quality", json=body).status_code == 401
    r = client.post("/mes/quality", json=body, headers={"X-Service-Token": "svc-token"})
    assert r.status_code == 200 and r.json()["status"] in ("staged", "duplicate")


def test_normalize_db_url():
    assert normalize_db_url("postgresql://u:p@h:5432/db") == "postgresql+psycopg://u:p@h:5432/db"
    assert normalize_db_url("postgres://u@h/db") == "postgresql+psycopg://u@h/db"
    assert normalize_db_url(" postgresql+psycopg://u@h/db ") == "postgresql+psycopg://u@h/db"
    assert normalize_db_url("sqlite:///x.db") == "sqlite:///x.db"


def test_refuses_to_start_without_token_when_required(monkeypatch):
    from fastapi.testclient import TestClient

    import main

    monkeypatch.setenv("AIVIS_REQUIRE_SERVICE_TOKEN", "true")
    monkeypatch.setattr(main, "get_settings", lambda: type(
        "S", (), {"service_token": None, "is_sqlite": True, "retention_enabled": False})())
    with pytest.raises(RuntimeError, match="AIVIS_SERVICE_TOKEN"):
        with TestClient(main.app):
            pass
