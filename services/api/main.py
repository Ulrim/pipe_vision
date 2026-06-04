"""AIVIS API 앱 조립 (CLAUDE.md §3,§4,§7).

라우터: auth, inspection, master, kpi, logs, mes, ws/live.
헬스체크 /health (compose healthcheck 계약).
sqlite/개발은 init_db 로 테이블 생성, 운영(postgres)은 Alembic.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from aivis_types import Role
from core.config import get_settings
from core.security import hash_password
from db.base import SessionLocal, engine, init_db
from db.models import AppUser
from routers import auth, inspection, kpi, logs, master, mes, ws_live


def _seed_admin() -> None:
    """부트스트랩 admin 시드(개발/초기 설치). 이미 있으면 건너뜀."""
    settings = get_settings()
    if not settings.seed_on_startup:
        return
    db = SessionLocal()
    try:
        if not db.get(AppUser, settings.seed_admin_user):
            db.add(
                AppUser(
                    username=settings.seed_admin_user,
                    pw_hash=hash_password(settings.seed_admin_password),
                    role=Role.ADMIN.value,
                    active=True,
                )
            )
            db.commit()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 개발/테스트(sqlite)는 테이블 자동 생성. 운영은 Alembic upgrade 로 관리.
    if get_settings().is_sqlite:
        init_db()
    _seed_admin()
    yield


app = FastAPI(
    title="AIVIS API",
    version="0.1.0",
    description="AI 머신비전 품질검사 백엔드 (검사결과 저장/조회/KPI/권한/로그/MES).",
    lifespan=lifespan,
)

app.include_router(auth.router)
app.include_router(inspection.router)
app.include_router(master.router)
app.include_router(kpi.router)
app.include_router(logs.router)
app.include_router(mes.router)
app.include_router(ws_live.router)


@app.get("/health", tags=["health"])
def health():
    """compose healthcheck 계약 엔드포인트."""
    settings = get_settings()
    db_ok = True
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
    except Exception:
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "db": "up" if db_ok else "down",
        "mes_mode": settings.mes_mode,
    }
