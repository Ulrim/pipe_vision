"""인증/권한/로그 스키마 (CLAUDE.md §5 M14,M15, §7.1)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from .enums import LogCategory, Role


class UserCreate(BaseModel):
    """사용자 등록 입력 (POST /auth/users, M14)."""

    username: str = Field(..., description="계정 ID(PK)")
    password: str = Field(..., min_length=4, description="비밀번호(평문 입력, 서버가 해시)")
    role: Role = Field(..., description="권한 역할")
    active: bool = Field(True, description="활성 여부")


class UserPublic(BaseModel):
    """사용자 공개 정보(비밀번호 제외)."""

    model_config = {"use_enum_values": True}

    username: str
    role: Role
    active: bool


class LoginRequest(BaseModel):
    """로그인 입력 (POST /auth/login)."""

    username: str
    password: str


class TokenResponse(BaseModel):
    """JWT 토큰 응답."""

    access_token: str
    token_type: str = "bearer"
    role: Role
    username: str


class SysLog(BaseModel):
    """시스템 로그 (sys_log 테이블, GET /logs, M15)."""

    model_config = {"use_enum_values": True}

    id: Optional[int] = None
    ts: Optional[datetime] = None
    level: Optional[str] = Field(None, description="로그 레벨(INFO/WARN/ERROR 등)")
    category: LogCategory = Field(..., description="로그 분류")
    message: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
