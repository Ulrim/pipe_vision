"""MES 자체 DB 엔진/세션 + AIVIS 인터페이스 DB 엔진."""
from __future__ import annotations

import logging
from typing import Iterator, Optional

from datetime import datetime, timezone

from sqlalchemy import DateTime, MetaData, create_engine, event, inspect, literal, text
from sqlalchemy.types import TypeDecorator
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from mes_app.config import get_settings

log = logging.getLogger("mes")


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


def _default_literal(col, dialect) -> Optional[str]:
    """컬럼 기본값을 DDL 리터럴로(그 DB 문법으로). 상수가 아니면 None.

    기본값은 server_default(DDL 에 박히는 값)와 default(파이썬이 INSERT 때 채우는 값) 두 군데에
    있을 수 있어 둘 다 본다. `func.now()` 같은 식은 sqlite 가 ADD COLUMN 에서 받지 않으므로 뺀다.
    """
    sd = getattr(col, "server_default", None)
    arg = getattr(sd, "arg", None)
    if isinstance(arg, str):
        return "'" + arg.replace("'", "''") + "'"
    if arg is not None:
        return None
    d = getattr(col, "default", None)
    if d is None or not getattr(d, "is_scalar", False):
        return None
    if not isinstance(d.arg, (bool, int, float, str)):
        return None
    return str(literal(d.arg).compile(dialect=dialect, compile_kwargs={"literal_binds": True}))


def reconcile_schema(eng: Optional[Engine] = None, metadata: Optional[MetaData] = None) -> list[str]:
    """이미 있는 MES 테이블에 **빠진 컬럼을 메운다.** 추가한 것을 `표.컬럼` 목록으로 돌려준다.

    `create_all()` 은 없는 테이블만 만든다. 모델에 컬럼을 더한 뒤 예전 DB 로 기동하면 그 표의
    조회·저장이 통째로 실패하므로, 기동할 때마다 모델과 DB 를 견줘 빠진 컬럼을 더한다
    (sqlite·Postgres 공통, 여러 번 불러도 같다).

    **추가 컬럼만** 다룬다. 삭제·타입 변경·제약 변경은 하지 않는다 — 그런 변경이 필요해지면
    마이그레이션 도구를 들인다(docs/MES.md §8.5). 기본값 없는 NOT NULL 컬럼은 기존 행을 채울
    값이 없어 더하지 못한다. 조용히 넘기면 같은 증상으로 돌아오므로 오류로 남긴다.
    """
    eng = eng or engine
    metadata = metadata if metadata is not None else Base.metadata
    insp = inspect(eng)
    existing = set(insp.get_table_names())
    quote = eng.dialect.identifier_preparer.quote
    added: list[str] = []
    with eng.begin() as conn:
        for table in metadata.sorted_tables:
            if table.name not in existing:
                continue  # create_all 이 만든다
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                default = _default_literal(col, eng.dialect)
                if not col.nullable and default is None:
                    log.error(
                        "MES 스키마: %s.%s 는 NOT NULL 인데 기본값이 없어 자동으로 추가할 수 없습니다."
                        " 이 컬럼을 쓰는 기능은 동작하지 않습니다(docs/MES.md §8.5).",
                        table.name, col.name,
                    )
                    continue
                ddl = (f"ALTER TABLE {quote(table.name)} ADD COLUMN {quote(col.name)} "
                       f"{col.type.compile(eng.dialect)}")
                if default is not None:
                    ddl += f" DEFAULT {default}"
                if not col.nullable:
                    ddl += " NOT NULL"
                conn.execute(text(ddl))
                added.append(f"{table.name}.{col.name}")
    if added:
        log.warning("MES 스키마: 예전 DB 에 빠진 컬럼을 추가했습니다: %s", ", ".join(added))
    return added


def init_db() -> None:
    """없는 테이블을 만들고, 있는 테이블에는 빠진 컬럼을 메운다."""
    from mes_app import models  # noqa: F401 - 테이블 등록

    Base.metadata.create_all(bind=engine)
    reconcile_schema()


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
