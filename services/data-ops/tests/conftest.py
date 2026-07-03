"""data-ops 단위테스트 픽스처: 임시 sqlite + dataset 임시 트리.

backend db 모델을 재사용(import)하되 정의/스키마는 변경하지 않는다.
"""
from __future__ import annotations

import os
import tempfile

import pytest

_TMPDIR = tempfile.mkdtemp(prefix="aivis_dataops_test_")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{os.path.join(_TMPDIR, 'do.db')}")
os.environ.setdefault("AIVIS_SEED_ON_STARTUP", "false")


@pytest.fixture
def db():
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
    from db.models import Inspection, ItemMaster, MesQualityIf, SysLog

    for model in (MesQualityIf, SysLog, Inspection, ItemMaster):
        db.query(model).delete()
    db.commit()
    db.add(ItemMaster(
        item_code="HP12", item_name="Header Pipe 12",
        ref_length_mm=250.0, tol_plus_mm=0.5, tol_minus_mm=0.5,
        px_to_mm_scale=0.05,
        oil_threshold=0.5, discolor_threshold=0.5, scratch_threshold=0.5,
    ))
    db.commit()
    yield


@pytest.fixture
def tmp_dataset(tmp_path):
    """부록 A.4 raw/<CLASS>/ 트리를 임시 생성하는 헬퍼 반환."""
    import json

    root = tmp_path / "dataset" / "raw"

    def _add(cls: str, fname: str, sidecar: dict | None = None):
        d = root / cls
        d.mkdir(parents=True, exist_ok=True)
        img = d / fname
        img.write_bytes(b"\xff\xd8\xff")  # 더미 JPEG 헤더
        if sidecar is not None:
            (d / (os.path.splitext(fname)[0] + ".json")).write_text(
                json.dumps(sidecar), encoding="utf-8"
            )
        return str(img)

    return root, _add
