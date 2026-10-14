"""인증 — AIVIS 와 로그인 하나.

MES 는 계정을 따로 두지 않는다. AIVIS(`POST /auth/login`)가 발급한 JWT 를 같은
서명키(`JWT_SECRET`)로 검증하고 역할(operator < quality < admin)을 그대로 쓴다.
대시보드는 이미 가진 토큰을 MES 요청에도 붙이기만 하면 된다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from mes_app.config import get_settings

_RANK = {"operator": 1, "quality": 2, "admin": 3}
_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class User:
    username: str
    role: str


def current_user(
    cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> User:
    if cred is None or not cred.credentials:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "로그인이 필요합니다")
    s = get_settings()
    try:
        payload = jwt.decode(cred.credentials, s.jwt_secret, algorithms=[s.jwt_algorithm])
    except JWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "토큰이 유효하지 않습니다") from None
    username, role = payload.get("sub"), payload.get("role")
    if not username or role not in _RANK:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "토큰이 유효하지 않습니다")
    return User(username=username, role=role)


def require_min_role(role: str) -> Callable[..., User]:
    need = _RANK[role]

    def _dep(user: User = Depends(current_user)) -> User:
        if _RANK[user.role] < need:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "권한이 없습니다")
        return user

    return _dep


def require_intake_token(request: Request) -> None:
    """REST 수신(AIVIS → MES) 토큰. MES_INTAKE_TOKEN 이 비어 있으면 검사하지 않는다
    (같은 호스트 안의 내부 호출 — AIVIS 의 POST /inspection 과 같은 정책)."""
    expected = get_settings().intake_token
    if not expected:
        return
    auth = request.headers.get("Authorization", "")
    presented = auth[7:] if auth.lower().startswith("bearer ") else ""
    if presented != expected:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "수신 토큰 불일치")
