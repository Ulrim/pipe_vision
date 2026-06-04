"""MES 단위테스트 픽스처: 임시 sqlite (라이브 postgres 불필요).

services/api 가 import 루트(pythonpath=.)이므로 db/core/mes 를 bare import 한다.
DATABASE_URL 을 임시 sqlite 로 강제한 뒤 모듈을 임포트해야 엔진이 sqlite 로 묶인다.
"""
from __future__ import annotations

import os
import tempfile

import pytest

_TMPDIR = tempfile.mkdtemp(prefix="aivis_mes_test_")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{os.path.join(_TMPDIR, 'mes.db')}")
os.environ.setdefault("AIVIS_SEED_ON_STARTUP", "false")
os.environ.setdefault("MES_MODE", "table")


@pytest.fixture
def db():
    """함수 단위 세션 + 테이블 보장."""
    from db.base import SessionLocal, init_db

    init_db()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(autouse=True)
def _clean(db):
    """각 테스트 전 핵심 테이블 비우기(파일 sqlite 공유 격리) + 품목 시드."""
    from db.models import Inspection, ItemMaster, MesQualityIf, SysLog

    for model in (MesQualityIf, SysLog, Inspection):
        db.query(model).delete()
    db.commit()
    # inspection.item_code FK 충족용 기준정보 시드(HP12).
    if not db.get(ItemMaster, "HP12"):
        db.add(ItemMaster(
            item_code="HP12", item_name="Header Pipe 12",
            ref_length_mm=250.0, tol_plus_mm=0.5, tol_minus_mm=0.5,
            px_to_mm_scale=0.05,
        ))
        db.commit()
    yield
