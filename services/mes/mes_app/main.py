"""AIVIS MES 앱 (uvicorn mes_app.main:app --port 8100)."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from mes_app import __doc__ as _doc
from mes_app.config import get_settings
from mes_app.db import SessionLocal, engine, init_db, interface_engine
from mes_app.intake import InterfaceReader, Poller
from mes_app.prefix import StripPathPrefix, path_prefix_from_env
from mes_app.routers import intake, logistics, orders

log = logging.getLogger("mes")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # MES 테이블은 create_all 로 만든다(없는 테이블만). 스키마 변경 규칙은 docs/MES.md §8.
    init_db()
    s = get_settings()
    app.state.poller = None
    eng = interface_engine()
    if eng is not None:
        poller = Poller(InterfaceReader(eng), SessionLocal,
                        interval_s=s.poll_interval_s, batch=s.poll_batch)
        app.state.poller = poller
        if os.getenv("MES_POLLER_ENABLED", "true").lower() not in ("0", "false", "no"):
            poller.start()
            log.info("MES 테이블 연계 폴링 시작(%.0fs 주기)", s.poll_interval_s)
    try:
        yield
    finally:
        if app.state.poller is not None:
            app.state.poller.stop()


app = FastAPI(title="AIVIS MES", version="0.1.0", description=_doc, lifespan=lifespan)

_s = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_s.allowed_origins,
    allow_credentials=_s.allowed_origins != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(orders.router)
app.include_router(logistics.router)
app.include_router(intake.router)

# 한 도메인 아래 `/mes-api` 로 물린 배포(Vercel services)는 경로를 그대로 넘긴다 — 앞의 접두어를 벗긴다.
# 마지막에 더해 가장 바깥에서 돈다. 접두어 없이 온 요청(바인딩 주소로 직접)은 그대로(mes_app/prefix.py).
app.add_middleware(StripPathPrefix, prefix=path_prefix_from_env())


@app.get("/health", tags=["health"])
def health():
    db_ok = True
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "db": "up" if db_ok else "down",
        "table_intake": get_settings().table_intake_enabled,
        "service": "mes",
    }
