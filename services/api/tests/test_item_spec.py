"""오더 교체용 치수 사양 입력 (PUT /master/items/{code}/spec).

제품 길이가 주문마다 바뀌는 공정이라 라인에서 직접 고쳐야 한다. 전체 갱신과
달리 **기준길이·공차만** 바꿀 수 있어야 하고, 변경은 추적 가능해야 한다.
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


def _spec(client, headers, code, **over):
    body = {"ref_length_mm": 250.0, "tol_plus_mm": 0.3, "tol_minus_mm": 0.3}
    body.update(over)
    return client.put(f"/master/items/{code}/spec", json=body, headers=headers)


def test_operator_can_change_length_and_tolerance(client, auth):
    """오더 교체는 작업자가 라인에서 한다 — 기본 권한이 작업자여야 한다."""
    _create(client, auth, "S_OP")
    r = _spec(client, auth("op1"), "S_OP")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ref_length_mm"] == 250.0
    assert d["tol_plus_mm"] == 0.3 and d["tol_minus_mm"] == 0.3


def test_version_increments_and_records_editor(client, auth):
    before = _create(client, auth, "S_VER")
    r = _spec(client, auth("op1"), "S_VER")
    assert r.json()["version"] == before["version"] + 1
    assert r.json()["updated_by"] == "op1"


def test_unchanged_values_do_not_bump_version(client, auth):
    """같은 값을 다시 넣었다고 이력이 불어나면 추적이 어려워진다."""
    before = _create(client, auth, "S_SAME")
    r = _spec(
        client, auth("op1"), "S_SAME",
        ref_length_mm=before["ref_length_mm"],
        tol_plus_mm=before["tol_plus_mm"],
        tol_minus_mm=before["tol_minus_mm"],
    )
    assert r.status_code == 200
    assert r.json()["version"] == before["version"]


def test_spec_cannot_touch_scale_or_thresholds(client, auth):
    """라인에서 급히 바꾸다 보정계수를 흔들면 길이가 통째로 틀어진다."""
    _create(client, auth, "S_NARROW", px_to_mm_scale=0.25, oil_threshold=0.4)
    r = client.put(
        "/master/items/S_NARROW/spec",
        json={
            "ref_length_mm": 300.0, "tol_plus_mm": 0.2, "tol_minus_mm": 0.2,
            "px_to_mm_scale": 99.0, "oil_threshold": 0.99,
        },
        headers=auth("op1"),
    )
    assert r.status_code == 200
    d = r.json()
    assert d["px_to_mm_scale"] == 0.25, "보정계수가 바뀌면 안 된다"
    assert d["oil_threshold"] == 0.4, "표면 임계가 바뀌면 안 된다"
    assert d["ref_length_mm"] == 300.0


def test_zero_tolerance_both_sides_rejected(client, auth):
    """양쪽 0 이면 전수 불량이 된다 — 입력 사고를 막아야 한다."""
    _create(client, auth, "S_ZERO")
    r = _spec(client, auth("op1"), "S_ZERO",
              tol_plus_mm=0.0, tol_minus_mm=0.0)
    assert r.status_code == 422


def test_tolerance_larger_than_length_rejected(client, auth):
    """공차가 제품보다 크면 자릿수를 잘못 친 것이다."""
    _create(client, auth, "S_BIG")
    r = _spec(client, auth("op1"), "S_BIG",
              ref_length_mm=250.0, tol_plus_mm=300.0)
    assert r.status_code == 422


def test_negative_or_zero_length_rejected(client, auth):
    _create(client, auth, "S_NEG")
    assert _spec(client, auth("op1"), "S_NEG",
                 ref_length_mm=0.0).status_code == 422
    assert _spec(client, auth("op1"), "S_NEG",
                 ref_length_mm=-5.0).status_code == 422


def test_partial_body_rejected(client, auth):
    """공차를 빼먹으면 이전 오더 값이 남는다 — 셋 다 받아야 한다."""
    _create(client, auth, "S_PART")
    r = client.put(
        "/master/items/S_PART/spec",
        json={"ref_length_mm": 300.0},
        headers=auth("op1"),
    )
    assert r.status_code == 422


def test_unknown_item_is_404(client, auth):
    assert _spec(client, auth("op1"), "NOPE").status_code == 404


def test_change_is_logged_with_before_and_after(client, auth):
    """판정이 이상할 때 언제 누가 무엇을 얼마에서 얼마로 바꿨는지 되짚어야 한다."""
    from db.base import SessionLocal
    from db.models import SysLog

    _create(client, auth, "S_LOG")
    _spec(client, auth("op1"), "S_LOG", ref_length_mm=400.0)
    db = SessionLocal()
    try:
        rows = db.query(SysLog).filter(SysLog.message.like("master.spec S_LOG%")).all()
    finally:
        db.close()
    assert rows, "사양 변경 로그가 없다"
    payload = rows[-1].payload or {}
    assert payload.get("before", {}).get("ref_length_mm") == 125.0
    assert payload.get("after", {}).get("ref_length_mm") == 400.0


def test_expected_count_can_be_set_with_spec(client, auth):
    """다발 개수도 오더마다 바뀐다 — 같은 화면에서 바꿀 수 있어야 한다."""
    _create(client, auth, "S_CNT")
    r = _spec(client, auth("op1"), "S_CNT", expected_count=24)
    assert r.status_code == 200
    assert r.json()["expected_count"] == 24


def test_expected_count_omitted_keeps_previous(client, auth):
    """생략하면 유지된다(개수 불일치는 NG 로 드러나므로 필수로 두지 않는다)."""
    _create(client, auth, "S_CNT2", expected_count=12)
    r = _spec(client, auth("op1"), "S_CNT2")
    assert r.json()["expected_count"] == 12


def test_expected_count_over_cap_rejected(client, auth):
    _create(client, auth, "S_CNT3")
    r = _spec(client, auth("op1"), "S_CNT3", expected_count=9999)
    assert r.status_code == 422


def test_default_min_role_is_operator():
    """작업자 기본값은 도입기업 확정 정책이다(2026-10-03) — 코드에서 조용히 못 바꾸게 못 박는다.

    권한을 넓힌 대신 수정 범위를 좁히고(위 테스트들) 전건을 감사 로그로 남기는
    설계라, 기본값만 슬쩍 quality 로 돌리면 라인이 서는 쪽으로 되돌아간다.
    """
    import os

    from aivis_types import Role
    from routers.master import _SPEC_EDIT_MIN_ROLE

    assert not os.getenv("AIVIS_SPEC_EDIT_MIN_ROLE"), (
        "이 테스트는 환경변수가 없을 때의 기본값을 검증한다"
    )
    assert _SPEC_EDIT_MIN_ROLE is Role.OPERATOR
