"""SQLAlchemy 2.0 엔진/세션/Base (CLAUDE.md §3,§7)."""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from core.config import get_settings


class Base(DeclarativeBase):
    """모든 ORM 모델의 선언적 베이스."""


def _make_engine():
    settings = get_settings()
    url = settings.database_url
    connect_args = {}
    if url.startswith("sqlite"):
        # sqlite: 멀티스레드(테스트/uvicorn) 허용 + FK 강제.
        connect_args = {"check_same_thread": False}
    engine = create_engine(url, future=True, pool_pre_ping=True, connect_args=connect_args)
    return engine


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Iterator[Session]:
    """FastAPI 의존성: 요청 단위 세션."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """sqlite/개발 환경에서 테이블 생성. 운영(postgres)은 Alembic 마이그레이션 사용."""
    from db import models  # noqa: F401  (모델 등록)
    from sqlalchemy import event

    if get_settings().is_sqlite:
        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _):  # pragma: no cover - 환경 의존
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    Base.metadata.create_all(bind=engine)
