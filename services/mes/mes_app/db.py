"""MES 자체 DB 엔진/세션 + AIVIS 인터페이스 DB 엔진."""
from __future__ import annotations

from typing import Iterator, Optional

from datetime import datetime, timezone

from sqlalchemy import DateTime, create_engine, event
from sqlalchemy.types import TypeDecorator
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from mes_app.config import get_settings


class UTCDateTime(TypeDecorator):
    """항상 UTC 시각(tz 포함)으로 돌려준다.

    sqlite 는 시간대를 저장하지 않아 읽으면 tz 없는 시각이 나오고, 그대로 응답하면
    브라우저가 UTC 를 한국시각으로 읽어 9시간 어긋나 보인다. 저장은 UTC 로 맞추고
    읽을 때 UTC 를 붙인다(Postgres timestamptz 에서도 같은 결과).
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if isinstance(value, datetime) and value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
            if dialect.name == "sqlite":
                value = value.replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class Base(DeclarativeBase):
    """MES ORM 베이스. 테이블은 모두 mes_ 접두어(같은 Postgres 에 두어도 섞이지 않게)."""


def make_engine(url: str) -> Engine:
    connect_args: dict = {}
    if url.startswith("sqlite"):
        # 독립형 파이: AIVIS 와 MES 가 같은 sqlite 파일(인터페이스)을 서로 다른
        # 프로세스에서 연다. 잠김을 기다리게 한다(즉시 'database is locked' 실패 방지).
        connect_args = {"check_same_thread": False, "timeout": 15}
    eng = create_engine(url, future=True, pool_pre_ping=True, connect_args=connect_args)
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _pragma(dbapi_conn, _rec):  # pragma: no cover - 드라이버 훅
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA busy_timeout=15000")
            cur.close()
    return eng


engine = make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

_iface_engine: Optional[Engine] = None


def interface_engine() -> Optional[Engine]:
    """AIVIS 인터페이스 DB 엔진(설정이 없으면 None)."""
    global _iface_engine
    url = get_settings().aivis_db_url
    if not url:
        return None
    if _iface_engine is None:
        _iface_engine = make_engine(url)
    return _iface_engine


def init_db() -> None:
    from mes_app import models  # noqa: F401 - 테이블 등록

    Base.metadata.create_all(bind=engine)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
