"""Supabase Storage — 서버 쪽 업로드 전달과 보관기한 정리 (2026-10-15, 클라우드 운영).

**업로드 전달**: 클라우드에서 현장 파이는 사진을 이 API 로 올리고(`PUT /inspection/images/
{key}`, 서비스 토큰) API 가 Supabase 로 넘긴다. 종전엔 파이가 Supabase 에 직접 올렸는데,
그러려면 파이마다 **service role key**(Supabase 전체 관리자 키 — DB 까지 무엇이든 읽고 쓴다)를
SD 카드에 두어야 했다. 파이를 잃어버리면 DB 가 통째로 열린다. 이제 키는 서버에만 있다.

**보관기한**: 생산 라인은 하루 수천 장을 찍는다. 지우는 규칙이 없으면 Supabase 저장 용량이
금방 찬다(로컬 SD 카드가 가득 찼던 것과 같은 문제 — core/retention). `raw/ result/ review/`
에서 `AIVIS_REMOTE_RETENTION_DAYS` 보다 오래된 객체를 지운다. 검사 기록(DB 행)은 남고
화면에는 "보관기간 경과" 로 보인다.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger("aivis.api.remote_storage")

PREFIXES = ("raw", "result", "review")
_PAGE = 1000
_DELETE_CHUNK = 100
_TIMEOUT_S = 15.0


class RemoteStorageError(Exception):
    """Supabase 호출 실패(네트워크·4xx·5xx)."""


def _base(settings) -> tuple[str, dict[str, str]]:
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise RemoteStorageError("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 가 설정되지 않았다")
    key = settings.supabase_service_role_key
    return settings.supabase_url.rstrip("/"), {"Authorization": f"Bearer {key}", "apikey": key}


def _client(client):
    if client is not None:
        return client, False
    import httpx

    return httpx.Client(timeout=_TIMEOUT_S), True


def put_object(settings, key: str, body: bytes, *, client=None) -> None:
    """객체 업로드(같은 키는 덮어쓴다 — 스풀 재전송이 같은 사진을 또 보낼 수 있다)."""
    base, headers = _base(settings)
    c, own = _client(client)
    try:
        resp = c.post(
            f"{base}/storage/v1/object/{settings.supabase_storage_bucket}/{key}",
            content=body,
            headers={**headers, "Content-Type": "image/jpeg", "x-upsert": "true"},
        )
    except Exception as exc:  # noqa: BLE001 - 네트워크 계층 전부
        raise RemoteStorageError(f"Supabase 업로드 전송 실패: {exc}") from exc
    finally:
        if own:
            c.close()
    if resp.status_code >= 400:
        raise RemoteStorageError(f"Supabase 업로드 실패 {resp.status_code}: {resp.text[:200]}")


def retention_days() -> int:
    try:
        return max(0, int(os.getenv("AIVIS_REMOTE_RETENTION_DAYS", "90")))
    except ValueError:
        return 90


def _parse_ts(v) -> Optional[datetime]:
    if not v:
        return None
    try:
        ts = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def cleanup(settings, *, days: Optional[int] = None, now: Optional[datetime] = None,
            client=None, max_pages: int = 20) -> dict[str, int]:
    """보관기한이 지난 사진을 지운다. {prefix: 지운 수}. days=0 이면 아무것도 안 한다.

    오래된 것부터 정렬해 읽고, 기한 안쪽 객체를 만나면 그 폴더는 멈춘다(매 회차 전체를
    훑지 않는다). 한 회차 상한 max_pages×1000 개 — 밀린 것은 다음 회차에 이어서 지운다.
    """
    days = retention_days() if days is None else days
    out = {p: 0 for p in PREFIXES}
    if days <= 0:
        return out
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    base, headers = _base(settings)
    bucket = settings.supabase_storage_bucket
    c, own = _client(client)
    try:
        for prefix in PREFIXES:
            for _ in range(max_pages):
                resp = c.post(
                    f"{base}/storage/v1/object/list/{bucket}",
                    json={"prefix": prefix, "limit": _PAGE, "offset": 0,
                          "sortBy": {"column": "created_at", "order": "asc"}},
                    headers=headers,
                )
                if resp.status_code >= 400:
                    raise RemoteStorageError(
                        f"Supabase 목록 실패 {resp.status_code}: {resp.text[:200]}")
                old: list[str] = []
                reached_young = False
                for obj in resp.json() or []:
                    ts = _parse_ts(obj.get("created_at"))
                    if obj.get("id") is None or ts is None:
                        continue  # 하위 폴더 표시 등
                    if ts >= cutoff:
                        reached_young = True
                        break
                    old.append(f"{prefix}/{obj['name']}")
                for i in range(0, len(old), _DELETE_CHUNK):
                    chunk = old[i:i + _DELETE_CHUNK]
                    d = c.request("DELETE", f"{base}/storage/v1/object/{bucket}",
                                  json={"prefixes": chunk}, headers=headers)
                    if d.status_code >= 400:
                        raise RemoteStorageError(
                            f"Supabase 삭제 실패 {d.status_code}: {d.text[:200]}")
                    out[prefix] += len(chunk)
                if reached_young or len(old) < _PAGE:
                    break
    finally:
        if own:
            c.close()
    if any(out.values()):
        log.info("클라우드 사진 보관기한(%d일) 정리: %s", days, out)
    return out
