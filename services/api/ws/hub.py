"""WebSocket 허브 (CLAUDE.md §5 M6, §7.4 WS /ws/live).

검사결과/알람을 연결된 작업자 HMI 로 실시간 푸시한다.
연결 관리 + broadcast. 연속 NG 카운트는 HMI/알람 모듈이 소비한다.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import WebSocket


class ConnectionHub:
    """활성 WebSocket 연결 풀 + 브로드캐스트."""

    def __init__(self) -> None:
        self._active: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._active.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._active.discard(ws)

    @property
    def count(self) -> int:
        return len(self._active)

    async def broadcast(self, message: dict[str, Any]) -> None:
        """모든 연결에 JSON 메시지 전송. 끊긴 연결은 정리."""
        dead: list[WebSocket] = []
        # set 복사로 안전 순회.
        for ws in list(self._active):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._active.discard(ws)


# 앱 전역 단일 허브.
hub = ConnectionHub()


def make_event(event: str, payload: dict[str, Any]) -> dict[str, Any]:
    """표준 WS 이벤트 봉투. event: inspection|alarm|... ."""
    return {"event": event, "data": payload}
