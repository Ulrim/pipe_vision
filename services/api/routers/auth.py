"""인증/권한 라우터 (CLAUDE.md §5 M14, §7.4)."""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from aivis_types import LoginRequest, Role, TokenResponse, UserCreate, UserPublic

from core.config import get_settings
from core.logging import write_log
from aivis_types import LogCategory
from core.security import (
    CurrentUser,
    create_access_token,
    hash_password,
    require_role,
    verify_password,
)
from db.base import get_db
from db.models import AppUser

router = APIRouter(prefix="/auth", tags=["auth"])


def _authenticate(db: Session, username: str, password: str) -> AppUser:
    user = db.get(AppUser, username)
    if not user or not user.active or not verify_password(password, user.pw_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="아이디 또는 비밀번호 오류"
        )
    return user


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """JSON 본문 로그인. JWT 발급."""
    user = _authenticate(db, body.username, body.password)
    token = create_access_token(user.username, user.role)
    write_log(db, category=LogCategory.USER, message=f"login {user.username}")
    return TokenResponse(
        access_token=token, role=Role(user.role), username=user.username
    )


# --- 키오스크 자동 로그인 — 파이 자체 화면 전용 -----------------------------
# 현장 문제: 작업자 화면(7인치 LCD)은 설비 앞 벽에 붙어 있고, 작업자는 장갑을
# 낀 채로 화면을 힐끗 본다. 그런데 토큰이 8시간(1교대)마다 만료돼 **교대마다
# 터치 키보드로 아이디·비밀번호를 입력**해야 했다 — 현장에서 쓸 수 없는 흐름이다.
#
# 그래서 **파이 자신의 화면(루프백에서 온 요청)** 에 한해 작업자 권한 토큰을
# 자동 발급한다. 판단 근거: 그 요청은 파이에 물리적으로 접근할 수 있는 사람만
# 만들 수 있고(공장 안), 사무실 PC 에서 파이 IP 로 접속하면 루프백이 아니라
# 여전히 로그인을 요구한다. 발급 권한도 **작업자(operator)** 로 한정해
# 기준정보 변경·프로그램 업데이트 같은 관리 동작은 불가능하다.
_KIOSK_USER = "kiosk"


def _is_loopback(request: Request) -> bool:
    """요청이 이 장비 자신(파이)에서 왔는가."""
    host = (request.client.host if request.client else "") or ""
    return host in ("127.0.0.1", "::1", "localhost")


@router.post("/kiosk", response_model=TokenResponse)
def kiosk_login(request: Request, db: Session = Depends(get_db)) -> TokenResponse:
    """파이 화면 자동 로그인(작업자 권한). 루프백 요청만 허용.

    비활성(AIVIS_KIOSK_AUTOLOGIN=false)이거나 외부 접속이면 403 — 클라이언트는
    조용히 일반 로그인 화면으로 넘어간다.
    """
    if not get_settings().kiosk_autologin:
        raise HTTPException(status_code=403, detail="키오스크 자동 로그인 비활성")
    if not _is_loopback(request):
        raise HTTPException(
            status_code=403, detail="이 장비의 화면에서만 사용할 수 있습니다"
        )
    user = db.get(AppUser, _KIOSK_USER)
    if not user or not user.active:
        # 계정이 없으면 이 시점에 만든다(설치 절차를 단순하게 유지).
        # 비밀번호는 무작위 — 이 계정으로는 일반 로그인을 하지 않는다.
        user = AppUser(
            username=_KIOSK_USER,
            pw_hash=hash_password(secrets.token_urlsafe(32)),
            role=Role.OPERATOR.value,
            active=True,
        )
        db.merge(user)
        db.commit()
        user = db.get(AppUser, _KIOSK_USER)
    token = create_access_token(user.username, user.role)
    return TokenResponse(
        access_token=token, role=Role(user.role), username=user.username
    )


@router.post("/login/oauth", response_model=TokenResponse)
def login_oauth(
    form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)
) -> TokenResponse:
    """OAuth2 password-form 호환(Swagger Authorize 버튼용)."""
    user = _authenticate(db, form.username, form.password)
    token = create_access_token(user.username, user.role)
    return TokenResponse(
        access_token=token, role=Role(user.role), username=user.username
    )


@router.post("/users", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
def create_user(
    body: UserCreate,
    db: Session = Depends(get_db),
    _admin: CurrentUser = Depends(require_role(Role.ADMIN)),
) -> UserPublic:
    """사용자 등록(관리자 전용)."""
    if db.get(AppUser, body.username):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="이미 존재하는 사용자"
        )
    role_value = body.role.value if isinstance(body.role, Role) else body.role
    user = AppUser(
        username=body.username,
        pw_hash=hash_password(body.password),
        role=role_value,
        active=body.active,
    )
    db.add(user)
    write_log(
        db,
        category=LogCategory.USER,
        message=f"create_user {body.username} role={role_value}",
        commit=False,
    )
    db.commit()
    return UserPublic(username=user.username, role=Role(user.role), active=user.active)


@router.get("/me", response_model=UserPublic)
def me(
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(require_role(Role.OPERATOR, Role.QUALITY, Role.ADMIN)),
) -> UserPublic:
    user = db.get(AppUser, current.username)
    if not user:
        raise HTTPException(status_code=404, detail="사용자 없음")
    return UserPublic(username=user.username, role=Role(user.role), active=user.active)
