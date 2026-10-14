"""MES 테스트: 임시 sqlite 두 개(MES DB · AIVIS 인터페이스 DB), 폴러는 수동 실행."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

_TMP = tempfile.mkdtemp(prefix="aivis_mes_test_")
os.environ["MES_DATABASE_URL"] = f"sqlite:///{os.path.join(_TMP, 'mes.db')}"
os.environ["MES_AIVIS_DB_URL"] = f"sqlite:///{os.path.join(_TMP, 'aivis.db')}"
os.environ["MES_POLLER_ENABLED"] = "false"
os.environ["JWT_SECRET"] = "mes-test-secret"
os.environ.pop("MES_INTAKE_TOKEN", None)

from fastapi.testclient import TestClient  # noqa: E402
from jose import jwt  # noqa: E402
from sqlalchemy import (  # noqa: E402
    JSON, Boolean, Column, DateTime, Integer, MetaData, Numeric, Table, Text, create_engine,
)

from mes_app.db import SessionLocal, init_db  # noqa: E402
from mes_app.main import app  # noqa: E402
from mes_app.models import (  # noqa: E402
    Claim, IntakeRun, QualityRecord, SalesOrder, Shipment, WorkOrder,
)


def iface_table(md: MetaData, *, new_columns: bool = True) -> Table:
    """AIVIS `mes_quality_if` 와 같은 모양(docs/MES_INTERFACE.md §2.1)."""
    cols = [
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("inspection_id", Integer),
        Column("lot", Text, nullable=False),
        Column("item_code", Text),
        Column("inspected_at", DateTime(timezone=True), nullable=False),
        Column("cam_id", Text, nullable=False),
        Column("idem_key", Text, nullable=False, unique=True),
        Column("work_order", Text),
        Column("final_verdict", Text, nullable=False),
        Column("defect_codes", JSON),
        Column("meas_length_mm", Numeric(10, 3)),
        Column("deviation_mm", Numeric(10, 3)),
        Column("consumed", Boolean, default=False),
        Column("retry_count", Integer, default=0),
        Column("created_at", DateTime(timezone=True)),
    ]
    if new_columns:
        cols += [
            Column("inspection_stage", Text),
            Column("tube_index", Integer),
            Column("manual_verdict", Text),
            Column("count_detected", Integer),
            Column("count_expected", Integer),
            Column("consumed_at", DateTime(timezone=True)),
            Column("updated_at", DateTime(timezone=True)),
        ]
    return Table("mes_quality_if", md, *cols)


init_db()

IFACE_ENGINE = create_engine(os.environ["MES_AIVIS_DB_URL"], future=True)
IFACE = iface_table(MetaData())
IFACE.metadata.create_all(IFACE_ENGINE)

T0 = datetime(2026, 10, 14, 0, 0, tzinfo=timezone.utc)


def iface_row(i: int, **over) -> dict:
    """인터페이스 행 하나(제품 1개)."""
    ts = over.pop("inspected_at", T0 + timedelta(seconds=i))
    lot = over.pop("lot", "L-1")
    cam = over.pop("cam_id", "CAM1")
    row = {
        "inspection_id": i, "lot": lot, "item_code": "HP12", "inspected_at": ts,
        "cam_id": cam, "idem_key": f"{lot}|HP12|{ts.isoformat()}|{cam}|{i}",
        "work_order": None, "final_verdict": "OK", "defect_codes": [],
        "meas_length_mm": 250.01, "deviation_mm": 0.01, "consumed": False,
        "inspection_stage": "CUT_LENGTH", "tube_index": i,
    }
    row.update(over)
    return row


def insert_iface(*rows: dict) -> None:
    # 한 행씩 — executemany 는 첫 행의 키만 쓰므로 행마다 컬럼이 다르면 값이 빠진다.
    with IFACE_ENGINE.begin() as conn:
        for row in rows:
            conn.execute(IFACE.insert().values(**row))


def token(username: str = "qa1", role: str = "quality") -> dict:
    t = jwt.encode({"sub": username, "role": role,
                    "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                   "mes-test-secret", algorithm="HS256")
    return {"Authorization": f"Bearer {t}"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clean():
    db = SessionLocal()
    try:
        for m in (Shipment, Claim, QualityRecord, WorkOrder, SalesOrder, IntakeRun):
            db.query(m).delete()
        db.commit()
    finally:
        db.close()
    with IFACE_ENGINE.begin() as conn:
        conn.execute(IFACE.delete())
    yield
