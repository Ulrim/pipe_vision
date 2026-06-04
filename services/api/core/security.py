"""인증/RBAC 유틸 (CLAUDE.md §5 M14). JWT + 비밀번호 해시 + 역할 가드."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

from aivis_types import Role

from core.config import get_settings

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)


def hash_password(plain: str) -> str:
    return _pwd.hash(plain)


def verify_password(plain: str, pw_hash: str) -> bool:
    try:
        return _pwd.verify(plain, pw_hash)
    except ValueError:
        return False


def create_access_token(username: str, role: str) -> str:
    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": username, "role": role, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


class CurrentUser:
    """디코드된 JWT 주체."""

    def __init__(self, username: str, role: str) -> None:
        self.username = username
        self.role = role


def get_current_user(token: str | None = Depends(oauth2_scheme)) -> CurrentUser:
    settings = get_settings()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="인증 토큰 없음"
        )
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="유효하지 않은 토큰"
        )
    username = payload.get("sub")
    role = payload.get("role")
    if not username or not role:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="토큰 클레임 누락"
        )
    return CurrentUser(username=username, role=role)


# RBAC 역할 위계: admin > quality > operator.
_ROLE_RANK = {Role.OPERATOR.value: 1, Role.QUALITY.value: 2, Role.ADMIN.value: 3}


def require_role(*allowed: Role):
    """지정 역할 이상만 허용하는 FastAPI 의존성 팩토리.

    allowed 에 포함된 역할 중 하나라도 만족하면 통과(정확 매칭).
    예: require_role(Role.QUALITY, Role.ADMIN).
    """
    allowed_values = {r.value for r in allowed}

    def _guard(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in allowed_values:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"권한 부족: {allowed_values} 필요 (현재 {user.role})",
            )
        return user

    return _guard


def require_min_role(minimum: Role):
    """최소 역할 위계 이상이면 허용(예: quality 이상 = quality+admin)."""
    min_rank = _ROLE_RANK[minimum.value]

    def _guard(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if _ROLE_RANK.get(user.role, 0) < min_rank:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"권한 부족: 최소 {minimum.value} 필요 (현재 {user.role})",
            )
        return user

    return _guard
