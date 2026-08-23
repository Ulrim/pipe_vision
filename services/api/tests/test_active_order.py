"""현재 검사 오더(/master/active) — 발주 기반 품목/LOT 전환 (M13 확장).

발주마다 품목·절단 길이가 달라지므로 웹에서 오더를 설정하면 워커가 폴링해
재시작 없이 전환한다. 여기서는 API 계약을 검증한다:
- 미설정 GET → 200 + null (404 아님).
- PUT(quality+) upsert 단일 행, 품목 미존재 404, 권한 가드.
- DELETE(quality+) 해제 → 이후 GET null.
"""
from __future__ import annotations


def _make_item(client, auth, code="HP12"):
    client.post(
        "/master/items",
        headers=auth("qa1"),
        json={
            "item_code": code,
            "item_name": f"Header Pipe {code}",
            "ref_length_mm": 250.0,
            "tol_plus_mm": 0.5,
            "tol_minus_mm": 0.5,
            "px_to_mm_scale": 0.05,
        },
    )


def test_get_unset_returns_null(client, auth):
    r = client.get("/master/active", headers=auth("op1"))
    assert r.status_code == 200, r.text
    assert r.json() is None


def test_put_and_get_roundtrip(client, auth):
    _make_item(client, auth, "HP12")
    r = client.put(
        "/master/active",
        headers=auth("qa1"),
        json={"item_code": "HP12", "lot": "LOT-A1", "work_order": "WO-7"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["item_code"] == "HP12"
    assert body["lot"] == "LOT-A1"
    assert body["work_order"] == "WO-7"
    assert body["updated_by"] == "qa1"
    assert body["updated_at"]

    g = client.get("/master/active", headers=auth("op1"))
    assert g.status_code == 200
    assert g.json()["item_code"] == "HP12"


def test_put_upserts_single_row(client, auth):
    """오더를 여러 번 바꿔도 행은 1개(id=1 upsert)여야 한다."""
    _make_item(client, auth, "HP12")
    _make_item(client, auth, "HP20")
    client.put("/master/active", headers=auth("qa1"),
               json={"item_code": "HP12", "lot": "L1"})
    client.put("/master/active", headers=auth("qa1"),
               json={"item_code": "HP20", "lot": "L2"})

    g = client.get("/master/active", headers=auth("op1")).json()
    assert g["item_code"] == "HP20"
    assert g["lot"] == "L2"

    from db.base import SessionLocal
    from db.models import ActiveOrder
    db = SessionLocal()
    try:
        rows = db.query(ActiveOrder).all()
        assert len(rows) == 1 and rows[0].id == 1
    finally:
        db.close()


def test_put_unknown_item_404(client, auth):
    r = client.put("/master/active", headers=auth("qa1"),
                   json={"item_code": "NOPE", "lot": "L"})
    assert r.status_code == 404


def test_permissions(client, auth):
    """PUT/DELETE 는 quality+, GET 은 operator 허용, 무인증 401."""
    _make_item(client, auth, "HP12")
    assert client.get("/master/active").status_code == 401
    r = client.put("/master/active", headers=auth("op1"),
                   json={"item_code": "HP12"})
    assert r.status_code == 403
    assert client.delete("/master/active", headers=auth("op1")).status_code == 403
    assert client.get("/master/active", headers=auth("op1")).status_code == 200


def test_delete_clears(client, auth):
    _make_item(client, auth, "HP12")
    client.put("/master/active", headers=auth("qa1"),
               json={"item_code": "HP12", "lot": "L1"})
    d = client.delete("/master/active", headers=auth("qa1"))
    assert d.status_code == 204
    assert client.get("/master/active", headers=auth("op1")).json() is None
    # 이미 비어 있어도 204(멱등).
    assert client.delete("/master/active", headers=auth("qa1")).status_code == 204
