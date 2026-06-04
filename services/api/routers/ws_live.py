"""실시간 검사결과/알람 푸시 (CLAUDE.md §5 M6, §7.4 WS /ws/live)."""
from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ws.hub import hub

router = APIRouter(tags=["ws"])


@router.websocket("/ws/live")
async def ws_live(ws: WebSocket):
    """HMI 실시간 채널. 서버->클라이언트 검사결과/알람 푸시.

    클라이언트는 keepalive ping 외 메시지를 보낼 필요가 없으나,
    수신 루프로 연결 생존을 감지한다.
    """
    await hub.connect(ws)
    try:
        while True:
            # 클라이언트 ping/keepalive 수신(없으면 대기). 끊기면 예외.
            await ws.receive_text()
    except WebSocketDisconnect:
        await hub.disconnect(ws)
    except Exception:
        await hub.disconnect(ws)
