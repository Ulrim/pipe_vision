"""MES 어댑터 멱등성/중복방지/모드 전환 테스트 (CLAUDE.md §7.3, §5 M9)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from db.models import Inspection, MesQualityIf
from mes.adapter import MesAdapter, make_idem_key_from_row
from mes.config import MesConfig
from mes.transport import FakeMesTransport, MesTransportError


def _table_cfg(**over) -> MesConfig:
    base = dict(
        mode="table", rest_url=None, rest_timeout_s=5.0,
        idem_header="X-Idempotency-Key", watchdog_interval_s=1.0,
        watchdog_batch_size=100, max_retry=8, backoff_base_s=0.1, backoff_max_s=1.0,
    )
    base.update(over)
    return MesConfig(**base)


def _rest_cfg(**over) -> MesConfig:
    return _table_cfg(mode="rest", **over)


def _mk_row(db, **over) -> Inspection:
    base = dict(
        lot="LOT1", item_code="HP12", cam_id="CAM1",
        inspected_at=datetime(2026, 6, 10, 8, 0, tzinfo=timezone.utc),
        final_verdict="OK", defect_codes=[], mes_synced=False,
        meas_length_mm=250.0, deviation_mm=0.0,
    )
    base.update(over)
    row = Inspection(**base)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_idem_key_format(db):
    """멱등키 = lot|item_code|inspected_at(iso)|cam_id (§7.3).

    inspected_at 의 타임존 직렬화는 DB 백엔드(sqlite vs postgres) 의존이라
    구조(파이프 4분할 + 양끝 식별자)만 단정한다.
    """
    row = _mk_row(db)
    key = make_idem_key_from_row(row)
    parts = key.split("|")
    assert len(parts) == 4
    assert parts[0] == "LOT1"
    assert parts[1] == "HP12"
    assert parts[2].startswith("2026-06-10T08:00:00")
    assert parts[3] == "CAM1"


def test_table_mode_stages_and_marks_synced(db):
    row = _mk_row(db)
    adapter = MesAdapter(_table_cfg())
    assert adapter.sync_row(db, row) is True
    db.refresh(row)
    assert row.mes_synced is True
    staged = db.query(MesQualityIf).filter_by(idem_key=make_idem_key_from_row(row)).all()
    assert len(staged) == 1


def test_table_mode_idempotent_no_duplicate(db):
    """동일 행 두 번 연계해도 mes_quality_if 는 1행(중복 적재 금지)."""
    row = _mk_row(db)
    adapter = MesAdapter(_table_cfg())
    adapter.sync_row(db, row)
    # 강제로 다시 미전송 표시 후 재연계
    row.mes_synced = False
    db.add(row)
    db.commit()
    adapter.sync_row(db, row)
    staged = db.query(MesQualityIf).filter_by(idem_key=make_idem_key_from_row(row)).all()
    assert len(staged) == 1


def test_rest_mode_success(db):
    row = _mk_row(db, cam_id="CAMR")
    fake = FakeMesTransport()
    adapter = MesAdapter(_rest_cfg(), transport=fake)
    assert adapter.sync_row(db, row) is True
    db.refresh(row)
    assert row.mes_synced is True
    assert make_idem_key_from_row(row) in fake.sent_keys
    staged = db.query(MesQualityIf).filter_by(idem_key=make_idem_key_from_row(row)).one()
    assert staged.consumed is True


def test_rest_mode_transient_failure_keeps_pending(db):
    """전송이 1회 실패하면 mes_synced=false 유지(워치독 재시도 대상)."""
    row = _mk_row(db, cam_id="CAMF")
    fake = FakeMesTransport(fail_times=1)
    adapter = MesAdapter(_rest_cfg(), transport=fake)
    assert adapter.sync_row(db, row) is False
    db.refresh(row)
    assert row.mes_synced is False
    # 두 번째 시도는 성공
    assert adapter.sync_row(db, row) is True
    db.refresh(row)
    assert row.mes_synced is True


def test_rest_mode_permanent_failure_logged(db):
    """영구 실패는 sys_log(category=mes)에 기록되고 미전송 유지."""
    from db.models import SysLog

    row = _mk_row(db, cam_id="CAMX")
    key = make_idem_key_from_row(row)
    fake = FakeMesTransport(fail_keys={key})
    adapter = MesAdapter(_rest_cfg(), transport=fake)
    assert adapter.sync_row(db, row) is False
    db.refresh(row)
    assert row.mes_synced is False
    logs = db.query(SysLog).filter_by(category="mes", level="ERROR").all()
    assert any(key in (l.message or "") for l in logs)
