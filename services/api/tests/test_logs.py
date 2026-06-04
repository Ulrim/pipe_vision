"""로그 적재/조회 (M15)."""
from __future__ import annotations


def test_login_writes_user_log_and_filter(client, auth):
    # 로그인은 user 카테고리 로그를 남긴다(conftest 외 추가 로그인).
    client.post("/auth/login", json={"username": "qa1", "password": "pw12345"})
    r = client.get("/logs", headers=auth("qa1"), params={"category": "user"})
    assert r.status_code == 200
    rows = r.json()
    assert all(x["category"] == "user" for x in rows)
    assert any("login" in (x["message"] or "") for x in rows)


def test_logs_requires_quality(client, auth):
    assert client.get("/logs", headers=auth("op1")).status_code == 403


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["db"] == "up"
