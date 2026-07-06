"""MES 워치독 재시도 + 연계 상태 테스트 (연계율 100% 보장, §7.3)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from db.models import Inspection
from mes.adapter import MesAdapter
from mes.config import MesConfig
from mes.transport import FakeMesTransport
from mes.watchdog import get_linkage_status, run_watchdog_forever, run_watchdog_once


def _table_cfg(**over) -> MesConfig:
    base = dict(
        mode="table", rest_url=None, rest_timeout_s=5.0,
        idem_header="X-Idempotency-Key", watchdog_interval_s=0.0,
        watchdog_batch_size=2, max_retry=8, backoff_base_s=0.1, backoff_max_s=1.0,
    )
    base.update(over)
    return MesConfig(**base)


def _seed(db, n, *, synced=False, start=0):
    """서로 다른 제품 n건 시드. start 로 자연키(lot/cam/inspected_at) 겹침 방지
    (inspection 은 ux_insp_natkey 유니크 — 동일 자연키 재시드는 실제로도 불가)."""
    t0 = datetime(2026, 6, 10, 8, 0, tzinfo=timezone.utc)
    for i in range(start, start + n):
        db.add(Inspection(
            lot=f"LOT{i}", item_code="HP12", cam_id=f"CAM{i}",
            inspected_at=t0 + timedelta(seconds=i),
            final_verdict="OK", defect_codes=[], mes_synced=synced,
        ))
    db.commit()


def test_linkage_status_rate(db):
    _seed(db, 3, synced=True)
    _seed(db, 1, synced=False, start=3)
    st = get_linkage_status(db, _table_cfg())
    assert st.total == 4
    assert st.synced == 3
    assert st.pending == 1
    assert st.rate == 75.0
    assert st.mode == "table"


def test_status_empty_is_100(db):
    st = get_linkage_status(db, _table_cfg())
    assert st.total == 0
    assert st.rate == 100.0


def test_watchdog_once_respects_batch(db):
    _seed(db, 5, synced=False)
    cfg = _table_cfg(watchdog_batch_size=2)
    res = run_watchdog_once(db, cfg=cfg)
    assert res.scanned == 2
    assert res.synced == 2
    assert res.failed == 0
    assert get_linkage_status(db, cfg).pending == 3


def test_watchdog_drains_to_100pct(db):
    """반복 워치독으로 미전송이 0이 되어 연계율 100% 달성."""
    _seed(db, 5, synced=False)
    cfg = _table_cfg(watchdog_batch_size=2)
    for _ in range(5):
        run_watchdog_once(db, cfg=cfg)
    st = get_linkage_status(db, cfg)
    assert st.pending == 0
    assert st.rate == 100.0


def test_watchdog_retries_transient_rest_failure(db):
    """rest 일시 실패 행을 다음 주기에 재시도해 결국 연계 성공."""
    _seed(db, 1, synced=False)
    fake = FakeMesTransport(fail_times=1)
    adapter = MesAdapter(_table_cfg(mode="rest"), transport=fake)
    r1 = run_watchdog_once(db, cfg=adapter.cfg, adapter=adapter)
    assert r1.failed == 1
    assert get_linkage_status(db, adapter.cfg).pending == 1
    r2 = run_watchdog_once(db, cfg=adapter.cfg, adapter=adapter)
    assert r2.synced == 1
    assert get_linkage_status(db, adapter.cfg).pending == 0


def test_watchdog_forever_max_cycles(db):
    """run_watchdog_forever 가 max_cycles 만큼 돌고 미전송을 비운다."""
    _seed(db, 3, synced=False)
    calls = {"n": 0}

    def _sleep(_s):
        calls["n"] += 1

    run_watchdog_forever(cfg=_table_cfg(watchdog_batch_size=1), max_cycles=3,
                         sleep_fn=_sleep)
    # 새 세션을 쓰므로 현재 db 세션은 만료 캐시 갱신
    db.expire_all()
    assert get_linkage_status(db, _table_cfg()).pending == 0
