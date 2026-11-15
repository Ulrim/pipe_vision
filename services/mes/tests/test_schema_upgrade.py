"""예전 MES DB 를 새 코드로 올렸을 때 빠진 컬럼을 메운다(docs/MES.md §8.5).

`create_all()` 은 없는 **테이블**만 만든다. 이미 있는 테이블에 컬럼을 더하지 않으므로,
모델에 컬럼을 추가한 뒤 예전 DB 로 기동하면 조회가 통째로 실패한다(AIVIS 쪽에서 실제로
났던 일 — services/api/tests/test_sqlite_schema_upgrade.py).
"""
from __future__ import annotations

import logging

import pytest
from sqlalchemy import (
    Boolean, Column, Integer, MetaData, Table, Text, create_engine, func, inspect, text,
)

from mes_app.db import UTCDateTime, reconcile_schema


def _legacy(md: MetaData) -> Table:
    return Table(
        "mes_demo", md,
        Column("id", Integer, primary_key=True),
        Column("name", Text, nullable=False),
    )


def _current(md: MetaData) -> Table:
    return Table(
        "mes_demo", md,
        Column("id", Integer, primary_key=True),
        Column("name", Text, nullable=False),
        Column("note", Text),                                          # NULL 허용
        Column("status", Text, nullable=False, default="OPEN"),        # 파이썬 기본값
        Column("qty", Integer, nullable=False, server_default="0"),    # DDL 기본값
        Column("urgent", Boolean, nullable=False, default=False),
        Column("created_at", UTCDateTime(), server_default=func.now()),  # 상수가 아닌 기본값
    )


@pytest.fixture()
def legacy(tmp_path):
    """예전 모양으로 만든 DB(행 1건) + 지금 모델의 메타데이터."""
    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}", future=True)
    old = MetaData()
    _legacy(old)
    old.create_all(eng)
    with eng.begin() as c:
        c.execute(text("INSERT INTO mes_demo (id, name) VALUES (1, 'A')"))
    new = MetaData()
    _current(new)
    yield eng, new
    eng.dispose()


def _cols(eng) -> set[str]:
    return {c["name"] for c in inspect(eng).get_columns("mes_demo")}


def test_adds_missing_columns(legacy):
    eng, md = legacy
    added = reconcile_schema(eng, md)
    assert set(added) == {
        "mes_demo.note", "mes_demo.status", "mes_demo.qty",
        "mes_demo.urgent", "mes_demo.created_at",
    }
    assert {"note", "status", "qty", "urgent", "created_at"} <= _cols(eng)


def test_existing_rows_kept_and_filled_with_defaults(legacy):
    eng, md = legacy
    reconcile_schema(eng, md)
    with eng.connect() as c:
        row = c.execute(text(
            "SELECT name, note, status, qty, urgent, created_at FROM mes_demo WHERE id=1"
        )).one()
    assert row[0] == "A"
    assert row[1] is None
    assert row[2] == "OPEN" and row[3] == 0 and not row[4]
    assert row[5] is None, "상수가 아닌 기본값은 기존 행에 채우지 않는다"


def test_idempotent(legacy):
    eng, md = legacy
    assert reconcile_schema(eng, md)
    assert reconcile_schema(eng, md) == []


def test_new_tables_are_left_to_create_all(tmp_path):
    """테이블 자체가 없으면 손대지 않는다 — create_all 이 만든다."""
    eng = create_engine(f"sqlite:///{tmp_path / 'empty.db'}", future=True)
    md = MetaData()
    _current(md)
    assert reconcile_schema(eng, md) == []
    assert "mes_demo" not in inspect(eng).get_table_names()
    eng.dispose()


def test_not_null_without_default_is_reported_not_added(legacy, caplog):
    """기본값 없는 NOT NULL 은 기존 행을 채울 값이 없다 — 조용히 넘기지 않고 남긴다."""
    eng, md = legacy
    md.tables["mes_demo"].append_column(Column("lot", Text, nullable=False))
    with caplog.at_level(logging.ERROR, logger="mes"):
        added = reconcile_schema(eng, md)
    assert "mes_demo.lot" not in added and "lot" not in _cols(eng)
    assert "mes_demo.lot" in caplog.text


def test_real_models_need_nothing_on_fresh_db():
    """실제 MES 모델: 방금 만든 DB 는 고칠 것이 없다(기동할 때마다 불린다)."""
    from mes_app.db import init_db

    init_db()
    assert reconcile_schema() == []
