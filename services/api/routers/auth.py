"""인증/권한 라우터 (CLAUDE.md §5 M14, §7.4)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from aivis_types import LoginRequest, Role, TokenResponse, UserCreate, UserPublic

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
