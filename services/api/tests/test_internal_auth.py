"""POST /inspection 내부 서비스 토큰 가드 (M14).

기본(토큰 미설정): 무인증 허용(사내 화이트리스트).
토큰 설정 시: X-Service-Token / Bearer 일치해야 허용, 아니면 401.
"""
from __future__ import annotations

from datetime import datetime, timezone


def _body(lot="SVC"):
    return {
        "lot": lot, "item_code": "SVCITEM", "cam_id": "C",
        "inspected_at": datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc).isoformat(),
        "final_verdict": "OK", "defect_codes": [], "review_flag": False,
        "mes_synced": False, "proc_time_ms": 100,
    }


def _seed_item(client, auth):
    client.post("/master/items", headers=auth("qa1"), json={
        "item_code": "SVCITEM", "item_name": "svc", "ref_length_mm": 100.0,
        "tol_plus_mm": 0.5, "tol_minus_mm": 0.5, "px_to_mm_scale": 0.05,
    })


def test_internal_open_when_token_unset(client, auth):
    """기본 설정(토큰 미설정)에서는 무인증으로 적재 가능."""
    _seed_item(client, auth)
    r = client.post("/inspection", json=_body("SVC0"))
    assert r.status_code == 201, r.text


def test_internal_requires_token_when_configured(client, auth, monkeypatch):
    """서비스 토큰 설정 시: 토큰 없으면 401, 헤더 일치 시 201."""
    _seed_item(client, auth)
    import core.security as sec

    class _S:
        service_token = "secret-token"

    monkeypatch.setattr(sec, "get_settings", lambda: _S())

    # 토큰 미제시 -> 401
    r = client.post("/inspection", json=_body("SVC1"))
    assert r.status_code == 401

    # X-Service-Token 일치 -> 201
    r = client.post("/inspection", json=_body("SVC2"),
                    headers={"X-Service-Token": "secret-token"})
    assert r.status_code == 201, r.text

    # Bearer 일치 -> 201
    r = client.post("/inspection", json=_body("SVC3"),
                    headers={"Authorization": "Bearer secret-token"})
    assert r.status_code == 201, r.text
