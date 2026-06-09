"""GET /inspection/{id}/images/{kind} Supabase Storage 백엔드 (M8, §7.4).

검증:
- storage_backend=="supabase" + 오브젝트 200 -> 200 image/jpeg 스트리밍(바이트 프록시).
- 404 -> 404. (스토리지 키/시크릿 미설정 -> 404.)
- operator+ JWT 가드 유지(미인증 401), 경로 None -> 404.
httpx 응답을 mock, get_settings lru_cache 회피 위해 routers.inspection 의
get_settings/httpx 심볼을 monkeypatch.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx


_JPEG_BYTES = b"\xff\xd8\xff\xe0SUPA\xff\xd9"


def _insp(**over):
    base = {
        "lot": "LOTSUPA",
        "item_code": "HP12",
        "cam_id": "CAMSUPA",
        "inspected_at": datetime(2026, 6, 10, 10, 0, tzinfo=timezone.utc).isoformat(),
        "final_verdict": "OK",
        "defect_codes": [],
        "review_flag": False,
        "mes_synced": False,
        "proc_time_ms": 130,
    }
    base.update(over)
    return base


def _make_item(client, auth):
    client.post(
        "/master/items",
        headers=auth("qa1"),
        json={
            "item_code": "HP12",
            "item_name": "Header Pipe 12",
            "ref_length_mm": 250.0,
            "tol_plus_mm": 0.5,
            "tol_minus_mm": 0.5,
            "px_to_mm_scale": 0.05,
        },
    )


def _store(client, auth, **over):
    _make_item(client, auth)
    r = client.post("/inspection", json=_insp(**over))
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _supabase_settings(monkeypatch, **over):
    """routers.inspection.get_settings 를 supabase 백엔드 stub 으로 교체."""
    import routers.inspection as insp_mod

    real = insp_mod.get_settings()

    class _S:
        storage_backend = "supabase"
        supabase_url = "https://proj.supabase.co"
        supabase_service_role_key = "service-role-key"
        supabase_storage_bucket = "inspection-images"

        def __getattr__(self, name):
            return getattr(real, name)

    stub = _S()
    for k, v in over.items():
        setattr(stub, k, v)
    monkeypatch.setattr(insp_mod, "get_settings", lambda: stub)
    return insp_mod


def test_supabase_200_streams_bytes(client, auth, monkeypatch):
    insp_mod = _supabase_settings(monkeypatch)
    insp_id = _store(client, auth, raw_image_path="raw/a.jpg")

    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return httpx.Response(200, content=_JPEG_BYTES)

    monkeypatch.setattr(insp_mod.httpx, "get", fake_get)

    r = client.get(f"/inspection/{insp_id}/images/raw", headers=auth("op1"))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content == _JPEG_BYTES
    # 오브젝트 키 + 인증 헤더 검증.
    assert captured["url"] == (
        "https://proj.supabase.co/storage/v1/object/inspection-images/raw/a.jpg"
    )
    assert captured["headers"]["Authorization"] == "Bearer service-role-key"
    assert captured["headers"]["apikey"] == "service-role-key"


def test_supabase_404_object_missing(client, auth, monkeypatch):
    insp_mod = _supabase_settings(monkeypatch)
    insp_id = _store(client, auth, lot="LOTSUPA404", raw_image_path="raw/missing.jpg")

    def fake_get(url, headers=None, timeout=None):
        return httpx.Response(404, content=b"not found")

    monkeypatch.setattr(insp_mod.httpx, "get", fake_get)

    r = client.get(f"/inspection/{insp_id}/images/raw", headers=auth("op1"))
    assert r.status_code == 404


def test_supabase_request_error_404(client, auth, monkeypatch):
    """타임아웃/네트워크 예외도 안전하게 404."""
    insp_mod = _supabase_settings(monkeypatch)
    insp_id = _store(client, auth, lot="LOTSUPAERR", raw_image_path="raw/x.jpg")

    def fake_get(url, headers=None, timeout=None):
        raise httpx.ConnectTimeout("boom")

    monkeypatch.setattr(insp_mod.httpx, "get", fake_get)

    r = client.get(f"/inspection/{insp_id}/images/raw", headers=auth("op1"))
    assert r.status_code == 404


def test_supabase_unconfigured_404(client, auth, monkeypatch):
    """SUPABASE_URL/KEY 미설정이면 404."""
    _supabase_settings(monkeypatch, supabase_url=None, supabase_service_role_key=None)
    insp_id = _store(client, auth, lot="LOTSUPANC", raw_image_path="raw/a.jpg")

    r = client.get(f"/inspection/{insp_id}/images/raw", headers=auth("op1"))
    assert r.status_code == 404


def test_supabase_missing_path_404(client, auth, monkeypatch):
    """경로 None 이면 supabase 호출 전 404."""
    _supabase_settings(monkeypatch)
    insp_id = _store(client, auth, lot="LOTSUPANP", raw_image_path="raw/only.jpg")

    r = client.get(f"/inspection/{insp_id}/images/result", headers=auth("op1"))
    assert r.status_code == 404


def test_supabase_unauthenticated_401(client, auth, monkeypatch):
    """JWT 가드 유지: 토큰 없으면 401."""
    _supabase_settings(monkeypatch)
    insp_id = _store(client, auth, lot="LOTSUPAUN", raw_image_path="raw/a.jpg")

    r = client.get(f"/inspection/{insp_id}/images/raw")
    assert r.status_code == 401
