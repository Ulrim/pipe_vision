"""예전 sqlite DB 를 새 코드로 열었을 때 스키마가 따라오는가.

현장에서 난 일: 파이에 예전 DB 가 깔려 있는데 코드만 업데이트했더니, 검사
결과 저장이 전부 이렇게 실패했다.

    inspection.store fail: (sqlite3.OperationalError)
    no such column: inspection.inspection_stage

원인은 `create_all()` 이 **없는 테이블만** 만들고 기존 테이블에는 컬럼을
추가하지 않는다는 점이다. 화면은 "검사 중" 으로 보이는데 DB 에는 한 건도
안 쌓인다 — 조용히 전부 실패하는 종류라 더 위험하다.
"""
from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import create_engine, inspect, text


def _legacy_db(path: str, drop_columns: set[str]) -> None:
    """현재 스키마에서 몇 컬럼을 뺀 '예전' DB 를 만든다."""
    import db.base as base

    eng = create_engine(f"sqlite:///{path}", future=True)
    insp_table = base.Base.metadata.tables["inspection"]
    cols = []
    for c in insp_table.columns:
        if c.name in drop_columns:
            continue
        piece = f"{c.name} {c.type.compile(eng.dialect)}"
        if c.primary_key:
            piece += " PRIMARY KEY"
        cols.append(piece)
    con = sqlite3.connect(path)
    con.execute(f"CREATE TABLE inspection ({', '.join(cols)})")
    con.commit()
    con.close()
    eng.dispose()


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    """예전 DB 를 가리키는 엔진으로 db.base 를 갈아끼운다."""
    import db.base as base

    path = tmp_path / "legacy.db"
    _legacy_db(str(path), {"inspection_stage", "tube_index"})

    eng = create_engine(f"sqlite:///{path}", future=True)
    monkeypatch.setattr(base, "engine", eng)
    yield base, eng, str(path)
    eng.dispose()


def test_legacy_db_really_is_missing_the_columns(legacy):
    """재현 자체를 먼저 고정한다 — 이게 깨지면 아래 테스트는 무의미하다."""
    _base, eng, _ = legacy
    have = {c["name"] for c in inspect(eng).get_columns("inspection")}
    assert "inspection_stage" not in have
    assert "tube_index" not in have


def test_reconcile_adds_the_missing_columns(legacy):
    base, eng, _ = legacy
    added = base.reconcile_sqlite_schema()
    assert "inspection.inspection_stage" in added
    assert "inspection.tube_index" in added
    have = {c["name"] for c in inspect(eng).get_columns("inspection")}
    assert "inspection_stage" in have and "tube_index" in have


def test_select_works_after_reconcile(legacy):
    """현장 증상의 직접 재현: 보정 전에는 조회가 깨지고, 보정 후에는 된다."""
    base, eng, _ = legacy
    q = text("SELECT inspection_stage, tube_index FROM inspection")
    with eng.connect() as c:
        with pytest.raises(Exception) as e:
            c.execute(q)
        assert "no such column" in str(e.value)

    base.reconcile_sqlite_schema()
    with eng.connect() as c:
        assert c.execute(q).fetchall() == []


def test_existing_rows_are_kept(legacy):
    """컬럼을 메우느라 데이터를 날리면 안 된다."""
    base, eng, _ = legacy
    with eng.begin() as c:
        c.execute(text(
            "INSERT INTO inspection (lot, cam_id, inspected_at, final_verdict)"
            " VALUES ('L1','CAM1','2026-10-03T00:00:00','OK')"
        ))
    base.reconcile_sqlite_schema()
    with eng.connect() as c:
        rows = c.execute(text("SELECT lot, final_verdict FROM inspection")).fetchall()
    assert rows == [("L1", "OK")]


def test_not_null_column_gets_its_server_default(legacy):
    """tube_index 는 NOT NULL + server_default='0' 이다. 기본값이 없으면
    sqlite 가 ADD COLUMN 자체를 거부하므로, 기본값을 붙여야 한다."""
    base, eng, _ = legacy
    with eng.begin() as c:
        c.execute(text(
            "INSERT INTO inspection (lot, cam_id, inspected_at, final_verdict)"
            " VALUES ('L2','CAM1','2026-10-03T00:00:00','NG')"
        ))
    base.reconcile_sqlite_schema()
    with eng.connect() as c:
        val = c.execute(text(
            "SELECT tube_index FROM inspection WHERE lot='L2'"
        )).scalar()
    assert val == 0, "기존 행이 기본값으로 채워져야 한다"


def test_is_idempotent(legacy):
    """두 번 돌려도 같은 결과여야 한다(기동할 때마다 불린다)."""
    base, _eng, _ = legacy
    first = base.reconcile_sqlite_schema()
    second = base.reconcile_sqlite_schema()
    assert first, "첫 실행에는 추가할 것이 있어야 한다"
    assert second == [], "두 번째에는 추가할 것이 없어야 한다"


def test_noop_on_a_current_schema(tmp_path, monkeypatch):
    """정상 DB 에서는 아무것도 건드리지 않는다."""
    import db.base as base

    eng = create_engine(f"sqlite:///{tmp_path/'fresh.db'}", future=True)
    monkeypatch.setattr(base, "engine", eng)
    base.Base.metadata.create_all(bind=eng)
    assert base.reconcile_sqlite_schema() == []
    eng.dispose()
