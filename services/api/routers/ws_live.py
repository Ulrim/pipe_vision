"""실시간 검사결과/알람 푸시 (CLAUDE.md §5 M6,M14, §7.4 WS /ws/live)."""
from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from jose import JWTError, jwt

from core.config import get_settings
from ws.hub import hub

router = APIRouter(tags=["ws"])


def _valid_token(token: str | None) -> bool:
    """쿼리 토큰을 core/security 와 동일한 JWT 정책으로 검증.

    sub/role 클레임이 모두 있어야 유효. 누락/디코드 실패/클레임 누락은 False.
    """
    if not token:
        return False
    settings = get_settings()
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except JWTError:
        return False
    return bool(payload.get("sub")) and bool(payload.get("role"))


@router.websocket("/ws/live")
async def ws_live(ws: WebSocket, token: str | None = None):
    """HMI 실시간 채널. 서버->클라이언트 검사결과/알람 푸시.

    사내 도구이므로 `?token=<JWT>` 쿼리 파라미터로 JWT 인증을 요구한다(M14).
    유효 토큰이면 accept 후 허브 구독, 무효/누락이면 accept 후 즉시 1008(policy
    violation)로 거절한다. 클라이언트는 keepalive ping 외 메시지를 보낼 필요가
    없으나, 수신 루프로 연결 생존을 감지한다.

    accept() 전에 close() 하면 WS 오프닝 핸드셰이크가 끝나지 않아 ASGI 서버가
    HTTP 403 으로 응답한다 — 브라우저 WebSocket 은 이를 code=1008 이 아니라
    비정상 종료(1006, "no close frame received")로 보고하므로, 프런트엔드의
    "1008=인증만료 → 로그인 화면 복귀" 처리가 발동하지 못하고 무한 재연결
    루프(HMI 상단 "재연결 중…" 고착)에 빠진다. 반드시 accept() 로 오프닝
    핸드셰이크를 먼저 완료해 실제 WS 종료 프레임으로 1008 을 전달해야 클라이언트
    가 정확한 코드를 받는다(로컬 검증: accept 후 close(1008) → 클라이언트
    close_code==1008 확인, accept 없이 close 만 하면 HTTP 403).
    """
    if not _valid_token(token):
        await ws.accept()
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await hub.connect(ws)
    try:
        while True:
            # 클라이언트 ping/keepalive 수신(없으면 대기). 끊기면 예외.
            await ws.receive_text()
    except WebSocketDisconnect:
        await hub.disconnect(ws)
    except Exception:
        await hub.disconnect(ws)
