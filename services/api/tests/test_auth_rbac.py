"""인증/RBAC 핵심 경로 (M14)."""
from __future__ import annotations


def test_login_success_and_fail(client):
    r = client.post("/auth/login", json={"username": "admin1", "password": "pw12345"})
    assert r.status_code == 200
    assert r.json()["role"] == "admin"

    bad = client.post("/auth/login", json={"username": "admin1", "password": "wrong"})
    assert bad.status_code == 401


def test_create_user_requires_admin(client, auth):
    # 작업자는 사용자 생성 불가(403)
    r = client.post(
        "/auth/users",
        headers=auth("op1"),
        json={"username": "newbie", "password": "pw12345", "role": "operator", "active": True},
    )
    assert r.status_code == 403

    # 관리자는 가능
    r = client.post(
        "/auth/users",
        headers=auth("admin1"),
        json={"username": "newbie", "password": "pw12345", "role": "operator", "active": True},
    )
    assert r.status_code == 201
    assert r.json()["username"] == "newbie"


def test_no_token_rejected(client):
    r = client.post(
        "/master/items",
        json={
            "item_code": "X1", "item_name": "x", "ref_length_mm": 1, "tol_plus_mm": 1,
            "tol_minus_mm": 1, "px_to_mm_scale": 1,
        },
    )
    assert r.status_code == 401


def test_master_write_requires_quality(client, auth):
    item = {
        "item_code": "RBAC1", "item_name": "x", "ref_length_mm": 100.0,
        "tol_plus_mm": 0.5, "tol_minus_mm": 0.5, "px_to_mm_scale": 0.05,
    }
    # 작업자 -> 403
    assert client.post("/master/items", headers=auth("op1"), json=item).status_code == 403
    # 품질관리자 -> 201
    assert client.post("/master/items", headers=auth("qa1"), json=item).status_code == 201


def test_master_version_increment(client, auth):
    item = {
        "item_code": "VER1", "item_name": "x", "ref_length_mm": 100.0,
        "tol_plus_mm": 0.5, "tol_minus_mm": 0.5, "px_to_mm_scale": 0.05,
    }
    client.post("/master/items", headers=auth("qa1"), json=item)
    r = client.put("/master/items/VER1", headers=auth("qa1"), json={"ref_length_mm": 101.0})
    assert r.status_code == 200
    assert r.json()["version"] == 2
    assert float(r.json()["ref_length_mm"]) == 101.0
