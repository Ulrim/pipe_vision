"""SQLAlchemy 2.0 엔진/세션/Base (CLAUDE.md §3,§7)."""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

import logging

from core.config import get_settings

log = logging.getLogger(__name__)


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


def _sqlite_default_literal(col) -> str | None:
    """컬럼의 기본값을 sqlite DDL 에 넣을 리터럴로. 없으면 None."""
    sd = getattr(col, "server_default", None)
    arg = getattr(sd, "arg", None)
    if arg is not None:
        return str(getattr(arg, "text", arg))

    d = getattr(col, "default", None)
    if d is None or not getattr(d, "is_scalar", False):
        return None          # 콜러블 기본값은 DDL 로 못 옮긴다
    v = d.arg
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    return None


def reconcile_sqlite_schema() -> list[str]:
    """기존 sqlite 테이블에 **빠진 컬럼을 메운다.** 추가된 것을 목록으로 반환.

    왜 필요한가: `create_all()` 은 **없는 테이블만** 만든다. 이미 있는 테이블에
    컬럼을 추가하지는 않는다. 그래서 예전 DB 가 깔린 파이를 새 코드로
    업데이트하면, 모델에는 있고 DB 에는 없는 컬럼이 생기고 **조회가 통째로
    실패한다.** 현장에서 실제로 났다:

        inspection.store fail: (sqlite3.OperationalError)
        no such column: inspection.inspection_stage

    검사 결과가 하나도 저장되지 않는데 화면은 "검사 중" 으로 보여, 원인을
    찾기도 어렵다.

    한계를 분명히 해 둔다 — 이것은 **추가 컬럼만** 처리한다(지금까지의 스키마
    변경이 모두 그랬다). 컬럼 삭제·타입 변경·제약 변경은 다루지 않는다.
    그런 변경이 필요해지면 sqlite 에서도 Alembic 을 돌려야 한다
    (`alembic upgrade head`). postgres 운영은 처음부터 Alembic 을 쓴다.
    """
    from sqlalchemy import inspect as sa_inspect, text

    added: list[str] = []
    insp = sa_inspect(engine)
    existing_tables = set(insp.get_table_names())

    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # create_all 이 방금 만들었거나 만들 것이다
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl = f"ALTER TABLE {table.name} ADD COLUMN {col.name} " \
                      f"{col.type.compile(engine.dialect)}"
                # sqlite 는 기존 행을 채워야 하므로 NOT NULL 에 기본값이 없으면
                # 추가 자체가 거부된다. 기본값이 있으면 그걸 쓴다.
                #
                # 기본값은 두 군데에 있을 수 있다. server_default(=DDL 에 박히는
                # 값)와 default(=파이썬이 INSERT 때 채우는 값). 모델이 후자를
                # 쓰는 컬럼이 있어서(tube_index 는 default=0) 둘 다 본다 —
                # 처음에 server_default 만 보다가 tube_index 를 놓쳤다.
                default = _sqlite_default_literal(col)
                if default is not None:
                    ddl += f" DEFAULT {default}"
                if not col.nullable and default is None:
                    # 기본값 없는 NOT NULL 은 추가할 수 없다. 조용히 넘기면
                    # 나중에 같은 증상으로 돌아오므로 남긴다.
                    log.warning(
                        "컬럼 %s.%s 는 NOT NULL 인데 기본값이 없어 자동 추가할 수"
                        " 없습니다. alembic upgrade head 가 필요합니다.",
                        table.name, col.name,
                    )
                    continue
                conn.execute(text(ddl))
                added.append(f"{table.name}.{col.name}")

    if added:
        log.warning("기존 sqlite 스키마에 빠진 컬럼을 추가했습니다: %s",
                    ", ".join(added))
    return added


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
    if get_settings().is_sqlite:
        # create_all 다음에 돌려야 한다 — 새 테이블은 이미 완전하고,
        # 기존 테이블만 손본다.
        reconcile_sqlite_schema()
