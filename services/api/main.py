"""AIVIS API 앱 조립 (CLAUDE.md §3,§4,§7).

라우터: auth, inspection, master, kpi, logs, mes, system, ws/live.
헬스체크 /health (compose healthcheck 계약).
sqlite/개발은 init_db 로 테이블 생성, 운영(postgres)은 Alembic.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from aivis_types import Role
from core.config import get_settings
from core.retention import cleanup_images, storage_usage
from core.security import hash_password
from db.base import SessionLocal, engine, init_db
from db.models import AppUser, ItemMaster
from routers import auth, inspection, kpi, logs, master, mes, system, ws_live


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


def _seed_demo_item() -> None:
    """데모 품목 시드(item_master FK 충족, §7.1). 멱등 — 이미 있으면 건너뜀.

    데모 배포에서 AIVIS_SEED_DEMO_ITEM=True 일 때만 동작. 워커가 검사결과를
    적재하려면 inspection.item_code 가 참조할 품목이 존재해야 한다.
    """
    settings = get_settings()
    if not settings.seed_demo_item:
        return
    db = SessionLocal()
    try:
        code = settings.demo_item_code
        if not db.get(ItemMaster, code):
            db.add(
                ItemMaster(
                    item_code=code,
                    item_name=f"Demo Header Pipe {code}",
                    ref_length_mm=125.0,
                    tol_plus_mm=0.5,
                    tol_minus_mm=0.5,
                    px_to_mm_scale=0.25,
                    oil_threshold=0.5,
                    discolor_threshold=0.5,
                    scratch_threshold=0.5,
                    capture_recipe={},
                    expected_count=1,
                    version=1,
                    updated_by="seed",
                )
            )
            db.commit()
    finally:
        db.close()


async def _retention_loop() -> None:
    """검사 이미지 정리를 주기적으로 돌린다(디스크 고갈 방지).

    지우는 규칙이 없어 현장 SD카드가 가득 찼던 문제 때문에 추가했다(하루 약
    8GB 씩 쌓인다). 기동 직후 한 번 돌려 이미 가득 찬 장비를 즉시 구제하고,
    이후 주기적으로 유지한다. 정리 실패가 검사를 멈추면 안 되므로 모든 예외를
    삼킨다.
    """
    settings = get_settings()
    interval_s = max(600, settings.retention_interval_s)
    log = logging.getLogger("aivis.api.retention")
    first = True
    while True:
        try:
            if first:
                # 기동 시 1회만 전체 사용량을 남긴다. 파일 수가 많으면 스캔이
                # 비싸므로 매 주기 반복하지 않는다(디스크 상태 진단용 기록).
                first = False
                log.info(
                    "이미지 저장소 상태: %s",
                    await asyncio.to_thread(storage_usage, settings.images_dir),
                )
            await asyncio.to_thread(cleanup_images, settings.images_dir)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("이미지 정리 실패(계속 진행): %s", exc)
        await asyncio.sleep(interval_s)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 개발/테스트(sqlite)는 테이블 자동 생성. 운영은 Alembic upgrade 로 관리.
    if get_settings().is_sqlite:
        init_db()
    _seed_admin()
    _seed_demo_item()
    task = None
    if get_settings().retention_enabled:
        task = asyncio.create_task(_retention_loop())
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


app = FastAPI(
    title="AIVIS API",
    version="0.1.0",
    description="AI 머신비전 품질검사 백엔드 (검사결과 저장/조회/KPI/권한/로그/MES).",
    lifespan=lifespan,
)

# CORS: 클라우드 데모(프론트 다른 출처)용 교차 출처 허용 (§3,§4).
# 명시 출처 목록이면 credentials 허용, "*" 이면 스펙상 credentials 불가.
_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.allowed_origins,
    allow_credentials=_settings.cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(inspection.router)
app.include_router(master.router)
app.include_router(master.active_router)
app.include_router(kpi.router)
app.include_router(logs.router)
app.include_router(mes.router)
app.include_router(system.router)
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
