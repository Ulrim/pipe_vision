"""클라우드 사진 저장(core/remote_storage) — Supabase 전달과 보관기한 정리 (2026-10-15)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx

from core import remote_storage

_NOW = datetime(2036, 6, 1, tzinfo=timezone.utc)


class _S:
    supabase_url = "https://proj.supabase.co/"
    supabase_service_role_key = "srk"
    supabase_storage_bucket = "inspection-images"


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_put_object_upserts_with_server_key():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"], seen["h"], seen["body"] = str(req.url), req.headers, req.content
        return httpx.Response(200, json={"Key": "x"})

    remote_storage.put_object(_S(), "raw/a.jpg", b"\xff\xd8x", client=_client(handler))
    assert seen["url"] == "https://proj.supabase.co/storage/v1/object/inspection-images/raw/a.jpg"
    assert seen["h"]["x-upsert"] == "true" and seen["h"]["authorization"] == "Bearer srk"
    assert seen["body"] == b"\xff\xd8x"


def test_put_object_raises_on_error():
    c = _client(lambda req: httpx.Response(500, text="nope"))
    try:
        remote_storage.put_object(_S(), "raw/a.jpg", b"\xff\xd8", client=c)
    except remote_storage.RemoteStorageError as exc:
        assert "500" in str(exc)
    else:
        raise AssertionError("실패를 삼키면 워커가 다시 보내지 않는다")


def test_cleanup_deletes_only_old_objects_per_folder():
    old = (_NOW - timedelta(days=100)).isoformat()
    young = (_NOW - timedelta(days=5)).isoformat()
    listing = {
        "raw": [{"name": "a.jpg", "id": "1", "created_at": old},
                {"name": "b.jpg", "id": "2", "created_at": old},
                {"name": "c.jpg", "id": "3", "created_at": young}],
        "result": [{"name": "x.jpg", "id": "4", "created_at": young}],
        "review": [],
    }
    deleted = []

    def handler(req: httpx.Request):
        body = json.loads(req.content or b"{}")
        if req.method == "POST" and "/object/list/" in str(req.url):
            assert body["sortBy"] == {"column": "created_at", "order": "asc"}
            return httpx.Response(200, json=listing[body["prefix"]])
        if req.method == "DELETE":
            deleted.extend(body["prefixes"])
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    out = remote_storage.cleanup(_S(), days=90, now=_NOW, client=_client(handler))
    assert out == {"raw": 2, "result": 0, "review": 0}
    assert deleted == ["raw/a.jpg", "raw/b.jpg"]


def test_cleanup_disabled_with_zero_days():
    def handler(req):  # pragma: no cover - 불리면 안 된다
        raise AssertionError("days=0 이면 호출하지 않는다")

    assert remote_storage.cleanup(_S(), days=0, client=_client(handler)) == {
        "raw": 0, "result": 0, "review": 0}
