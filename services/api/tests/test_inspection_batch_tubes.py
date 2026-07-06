"""다중 튜브 배치 저장 (한 프레임의 튜브 N개 = tube_index 0..N-1 별도 행).

같은 배치 식별자(lot/item_code/cam_id/inspected_at)를 공유하는 튜브들을
tube_index 로 구분해 각각 저장한다(§7.1 자연키 확장). 검증:
- tube_index 0..12 POST → 13행 저장·각 조회.
- 동일 tube_index 재전송은 멱등(행 안 늘어남, 같은 id).
- 단일 튜브(tube_index 기본 0)는 기존 동작 유지.
- MES 스테이징 멱등키가 튜브별로 분리(배치 튜브 수 = 스테이징 행 수).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest


def _insp(**over):
    base = {
        "lot": "LOTBATCH",
        "item_code": "BATCH",
        "cam_id": "CAMBATCH",
        "inspected_at": datetime(
            2026, 7, 6, 10, 0, 0, 500000, tzinfo=timezone.utc
        ).isoformat(),
        "final_verdict": "OK",
        "defect_codes": [],
        "review_flag": False,
        "mes_synced": False,
        "proc_time_ms": 120,
    }
    base.update(over)
    return base


@pytest.fixture
def seeded(client, auth, monkeypatch):
    """hub.broadcast 무력화 + FK 시드 + 연속 NG 카운터 리셋."""
    import ws.hub as hub_mod
    from ws.alarm import tracker

    client.post("/master/items", headers=auth("qa1"), json={
        "item_code": "BATCH", "item_name": "batch", "ref_length_mm": 100.0,
        "tol_plus_mm": 0.5, "tol_minus_mm": 0.5, "px_to_mm_scale": 0.05,
        "expected_count": 13,
    })

    async def _noop(message):
        return None

    monkeypatch.setattr(hub_mod.hub, "broadcast", _noop)
    tracker.reset()
    yield
    tracker.reset()


def _rows(client, auth, lot: str) -> list[dict]:
    r = client.get("/inspection", headers=auth("op1"), params={"lot": lot})
    assert r.status_code == 200, r.text
    return r.json()


def test_batch_tubes_saved_as_separate_rows(client, auth, seeded):
    ids = []
    for ti in range(13):
        r = client.post("/inspection", json=_insp(tube_index=ti))
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "stored"
        ids.append(r.json()["id"])

    assert len(set(ids)) == 13  # 13개 고유 행

    rows = _rows(client, auth, "LOTBATCH")
    assert len(rows) == 13
    saved_tubes = sorted(row["tube_index"] for row in rows)
    assert saved_tubes == list(range(13))


def test_same_tube_index_resend_is_idempotent(client, auth, seeded):
    payload = _insp(lot="LOTBATCHRE", tube_index=3)
    r1 = client.post("/inspection", json=payload)
    r2 = client.post("/inspection", json=payload)
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]  # 같은 행 반환

    rows = _rows(client, auth, "LOTBATCHRE")
    assert len(rows) == 1
    assert rows[0]["tube_index"] == 3


def test_single_tube_default_index_zero(client, auth, seeded):
    # tube_index 미지정 → 기본 0, 기존 단일 튜브 동작.
    payload = _insp(lot="LOTBATCHONE")
    r1 = client.post("/inspection", json=payload)
    r2 = client.post("/inspection", json=payload)  # 재전송 멱등
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]

    rows = _rows(client, auth, "LOTBATCHONE")
    assert len(rows) == 1
    assert rows[0]["tube_index"] == 0


def test_mes_staging_separated_per_tube(client, auth, seeded):
    from db.base import SessionLocal
    from db.models import MesQualityIf

    for ti in range(5):
        r = client.post("/inspection", json=_insp(lot="LOTBATCHMES", tube_index=ti))
        assert r.status_code == 201, r.text
    # 동일 tube_index 재전송은 스테이징 중복 없음.
    client.post("/inspection", json=_insp(lot="LOTBATCHMES", tube_index=0))

    db = SessionLocal()
    try:
        rows = (
            db.query(MesQualityIf)
            .filter(MesQualityIf.lot == "LOTBATCHMES")
            .all()
        )
        assert len(rows) == 5  # 튜브별 5개 스테이징 행
        assert len({row.idem_key for row in rows}) == 5  # 멱등키 튜브별 분리
    finally:
        db.close()
