"""독립형 sqlite 온라인 백업(2026-10-10 점검: "파이 단독 SQLite 백업 없음")."""
from __future__ import annotations

import gzip
import importlib.util
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "_backup_sqlite", Path(__file__).resolve().parents[1] / "tools" / "backup_sqlite.py"
)
bk = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(bk)


def _db(tmp_path: Path) -> Path:
    p = tmp_path / "aivis.db"
    c = sqlite3.connect(p)
    c.execute("create table inspection(id integer primary key, lot text)")
    c.executemany("insert into inspection(lot) values (?)", [(f"L{i}",) for i in range(50)])
    c.commit()
    c.close()
    return p


def test_backup_is_consistent_and_restorable_while_writer_open(tmp_path):
    db = _db(tmp_path)
    writer = sqlite3.connect(db)  # 검사가 돌고 있는 상황
    writer.execute("insert into inspection(lot) values ('LIVE')")
    writer.commit()
    out = bk.backup(db, tmp_path / "bk", keep=5)
    writer.close()
    restored = tmp_path / "restored.db"
    restored.write_bytes(gzip.decompress(out.read_bytes()))
    c = sqlite3.connect(restored)
    assert c.execute("select count(*) from inspection").fetchone()[0] == 51
    assert c.execute("pragma integrity_check").fetchone()[0] == "ok"
    c.close()
    assert out.name.startswith("aivis_") and out.name.endswith(".db.gz")
    assert not list((tmp_path / "bk").glob("*.part")), "반쯤 쓴 파일이 남으면 안 된다"


def test_keeps_only_latest_n(tmp_path):
    db = _db(tmp_path)
    base = datetime(2026, 10, 10, 2, 30, tzinfo=timezone(timedelta(hours=9)))
    for d in range(5):
        bk.backup(db, tmp_path / "bk", keep=3, now=base + timedelta(days=d))
    names = [p.name for p in bk.list_backups(tmp_path / "bk")]
    assert names == ["aivis_20261012_023000.db.gz", "aivis_20261013_023000.db.gz",
                     "aivis_20261014_023000.db.gz"]


def test_missing_db_fails_loudly(tmp_path):
    assert bk.main(["--db", str(tmp_path / "nope.db"), "--out", str(tmp_path / "bk")]) == 1


def test_mes_db_keeps_its_own_prefix_and_retention(tmp_path):
    """MES DB(mes_)와 검사 DB(aivis_)는 같은 폴더에 두어도 이름·보관 개수를 따로 센다."""
    backup, list_backups = bk.backup, bk.list_backups
    for name in ("aivis.db", "mes.db"):
        c = sqlite3.connect(tmp_path / name)
        c.execute("create table t(x)")
        c.commit()
        c.close()
    out = tmp_path / "bk"
    t0 = datetime(2036, 1, 1, tzinfo=timezone.utc)
    for i in range(3):
        backup(tmp_path / "aivis.db", out, keep=2, now=t0 + timedelta(seconds=i))
    m = backup(tmp_path / "mes.db", out, keep=2, now=t0, prefix="mes_")
    assert m.name.startswith("mes_")
    assert len(list_backups(out)) == 2, "검사 DB 보관 개수는 MES 백업과 섞이지 않는다"
    assert len(list_backups(out, "mes_")) == 1
