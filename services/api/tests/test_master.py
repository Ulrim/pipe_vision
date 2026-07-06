"""기준정보 오더 설정 필드 + 웹 캘리브레이션 테스트 (§5 M13).

- expected_count / outer_diameter_mm CRUD 왕복, expected_count 기본 1
- POST /master/items/{code}/calibrate: scale×actual/measured, version 증가,
  잘못된 입력 422, 권한(operator 금지).
"""
from __future__ import annotations


def _create(client, auth, code: str, **over):
    body = {
        "item_code": code,
        "item_name": f"Item {code}",
        "ref_length_mm": 125.0,
        "tol_plus_mm": 0.5,
        "tol_minus_mm": 0.5,
        "px_to_mm_scale": 0.25,
    }
    body.update(over)
    r = client.post("/master/items", json=body, headers=auth("qa1"))
    assert r.status_code == 201, r.text
    return r.json()


def test_expected_count_defaults_to_one(client, auth):
    """expected_count 미지정 시 기본 1, outer_diameter_mm 기본 None."""
    data = _create(client, auth, "M_DEF")
    assert data["expected_count"] == 1
    assert data["outer_diameter_mm"] is None


def test_new_fields_create_roundtrip(client, auth):
    """다중 튜브 + 외경 지정 왕복(create→get→list)."""
    _create(client, auth, "M_MULTI", expected_count=4, outer_diameter_mm=12.7)

    g = client.get("/master/items/M_MULTI", headers=auth("op1"))
    assert g.status_code == 200, g.text
    assert g.json()["expected_count"] == 4
    assert g.json()["outer_diameter_mm"] == 12.7

    lst = client.get("/master/items", headers=auth("op1")).json()
    row = next(x for x in lst if x["item_code"] == "M_MULTI")
    assert row["expected_count"] == 4
    assert row["outer_diameter_mm"] == 12.7


def test_new_fields_update_roundtrip(client, auth):
    """PUT 부분 갱신으로 expected_count/outer_diameter_mm 변경 + version 증가."""
    _create(client, auth, "M_UPD", expected_count=1)
    r = client.put(
        "/master/items/M_UPD",
        json={"expected_count": 8, "outer_diameter_mm": 9.5},
        headers=auth("qa1"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["expected_count"] == 8
    assert r.json()["outer_diameter_mm"] == 9.5
    assert r.json()["version"] == 2


def test_calibrate_computes_new_scale(client, auth):
    """새 scale = 기존 × (actual/measured), version 증가, updated_by 기록."""
    _create(client, auth, "M_CAL", px_to_mm_scale=0.25)
    # 시스템 측정 100mm, 실제 102mm → scale 커져야 함.
    r = client.post(
        "/master/items/M_CAL/calibrate",
        json={"measured_mm": 100.0, "actual_mm": 102.0},
        headers=auth("qa1"),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert abs(data["px_to_mm_scale"] - 0.25 * (102.0 / 100.0)) < 1e-9
    assert data["version"] == 2
    assert data["updated_by"] == "qa1"


def test_calibrate_invalid_input_422(client, auth):
    """measured_mm=0 / 음수는 gt=0 스키마로 422."""
    _create(client, auth, "M_CAL2")
    for bad in ({"measured_mm": 0.0, "actual_mm": 100.0},
                {"measured_mm": 100.0, "actual_mm": -1.0}):
        r = client.post(
            "/master/items/M_CAL2/calibrate", json=bad, headers=auth("qa1")
        )
        assert r.status_code == 422, r.text


def test_calibrate_missing_item_404(client, auth):
    r = client.post(
        "/master/items/NOPE/calibrate",
        json={"measured_mm": 100.0, "actual_mm": 100.0},
        headers=auth("qa1"),
    )
    assert r.status_code == 404


def test_calibrate_requires_quality_role(client, auth):
    """operator 는 캘리브레이션 금지(quality+ 필요)."""
    _create(client, auth, "M_CAL3")
    r = client.post(
        "/master/items/M_CAL3/calibrate",
        json={"measured_mm": 100.0, "actual_mm": 100.0},
        headers=auth("op1"),
    )
    assert r.status_code == 403, r.text
