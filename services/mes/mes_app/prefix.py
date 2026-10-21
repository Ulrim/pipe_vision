"""경로 접두어 벗기기 — 한 도메인 아래 `/mes-api` 로 물린 배포(Vercel services, 2026-10-21).

Vercel 은 `/mes-api/(.*)` 를 이 서비스로 보낼 때 요청 경로를 **그대로**(`/mes-api/orders`) 넘긴다.
라우터는 `/orders` 로 정의돼 있으므로 앞의 `/mes-api` 를 벗겨 라우팅한다. 접두어 없이 온 요청
(AIVIS API 가 바인딩 주소로 직접, 현장 단독·Render)은 손대지 않는다 — 두 경우가 같은 코드로 돈다.

접두어: `MES_PATH_PREFIX`(예 `/mes-api`). 비우면 Vercel(`VERCEL=1`)에서는 `/mes-api`, 그 밖에서는 없음.
HTTP 와 WebSocket 모두. `root_path` 를 남겨 OpenAPI 문서 주소도 맞는다.
"""
from __future__ import annotations

import os
from typing import Mapping, Optional


def path_prefix_from_env(env: Optional[Mapping[str, str]] = None, *, vercel_default: str = "/mes-api") -> str:
    e = os.environ if env is None else env
    raw = (e.get("MES_PATH_PREFIX") or "").strip()
    if not raw:
        raw = vercel_default if e.get("VERCEL") else ""
    raw = raw.rstrip("/")
    if raw and not raw.startswith("/"):
        raw = "/" + raw
    return raw


class StripPathPrefix:
    """ASGI 미들웨어 — scope 의 path(·raw_path)에서 접두어를 벗기고 root_path 에 더한다."""

    def __init__(self, app, prefix: str = "") -> None:
        self.app = app
        self.prefix = prefix.rstrip("/")
        self._raw = self.prefix.encode("latin-1")

    async def __call__(self, scope, receive, send):
        p = self.prefix
        if p and scope.get("type") in ("http", "websocket"):
            path = scope.get("path", "")
            if path == p or path.startswith(p + "/"):
                scope = dict(scope)
                scope["path"] = path[len(p):] or "/"
                raw = scope.get("raw_path")
                if isinstance(raw, (bytes, bytearray)) and raw.startswith(self._raw):
                    scope["raw_path"] = bytes(raw[len(self._raw):]) or b"/"
                scope["root_path"] = scope.get("root_path", "") + p
        await self.app(scope, receive, send)
