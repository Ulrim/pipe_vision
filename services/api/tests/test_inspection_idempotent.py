"""POST /inspection 자연키 멱등 (M7 — 엣지 오프라인 스풀 재전송 대응).

자연키 = (lot, item_code, inspected_at, cam_id) — MES idem_key(§7.3)와 동일 구성.
검증:
- 동일 payload 2회 POST → 행 1개, 같은 id, 두 번째는 WS 브로드캐스트 없음.
- 중복 NG 재전송은 ng 알람/연속 NG 카운터를 이중 발화하지 않음.
- inspected_at 이 다르면 정상 2행.
- 동시성 레이스(사전 조회 miss): 유니크 인덱스 IntegrityError → 기존 행 반환.
- 자연키와 무관한 IntegrityError(FK 위반)는 기존대로 로컬 큐 백업(queued).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest


def _insp(**over):
    base = {
        "lot": "LOTIDEM",
        "item_code": "IDEM",
        "cam_id": "CAMIDEM",
        # ms 정밀도 타임스탬프(엣지 워커와 동일).
        "inspected_at": datetime(
            2026, 7, 3, 8, 0, 0, 123000, tzinfo=timezone.utc
        ).isoformat(),
        "final_verdict": "OK",
        "defect_codes": [],
        "review_flag": False,
        "mes_synced": False,
        "proc_time_ms": 110,
    }
    base.update(over)
    return base


@pytest.fixture
def captured(client, auth, monkeypatch):
    """hub.broadcast 캡처 + FK 시드 + 연속 NG 카운터 리셋 (test_alarm 동형)."""
    import ws.hub as hub_mod
    from ws.alarm import tracker

    client.post("/master/items", headers=auth("qa1"), json={
        "item_code": "IDEM", "item_name": "idem", "ref_length_mm": 100.0,
        "tol_plus_mm": 0.5, "tol_minus_mm": 0.5, "px_to_mm_scale": 0.05,
    })

    events: list[dict] = []

    async def _capture(message):
        events.append(message)

    monkeypatch.setattr(hub_mod.hub, "broadcast", _capture)
    tracker.reset()
    yield events
    tracker.reset()


def _count_rows(client, auth, lot: str) -> list[dict]:
    r = client.get("/inspection", headers=auth("op1"), params={"lot": lot})
    assert r.status_code == 200
    return r.json()


def test_duplicate_post_returns_same_row_without_broadcast(client, auth, captured):
    payload = _insp()
    r1 = client.post("/inspection", json=payload)
    assert r1.status_code == 201, r1.text
    assert r1.json()["status"] == "stored"
    id1 = r1.json()["id"]
    n_events_after_first = len(captured)
    assert n_events_after_first >= 1  # inspection 이벤트 발행됨

    # 재전송(서버 저장 후 응답 유실 시나리오): 동일 payload 그대로.
    r2 = client.post("/inspection", json=payload)
    assert r2.status_code == 201, r2.text
    body = r2.json()
    assert body["status"] == "stored"          # 기존 응답 스키마 유지
    assert body["id"] == id1                    # 기존 행 id 반환
    assert body["inspection"]["id"] == id1
    assert len(captured) == n_events_after_first  # 두 번째는 브로드캐스트 없음

    rows = _count_rows(client, auth, "LOTIDEM")
    assert len(rows) == 1                       # 행 1개(중복 생성 없음)
    assert rows[0]["id"] == id1


def test_duplicate_ng_does_not_double_alarm_or_count(client, auth, captured):
    from ws.alarm import tracker

    payload = _insp(lot="LOTIDEMNG", final_verdict="NG", defect_codes=["SCR"])
    for _ in range(2):
        r = client.post("/inspection", json=payload)
        assert r.status_code == 201, r.text

    ng_alarms = [
        e for e in captured
        if e.get("event") == "alarm" and e.get("data", {}).get("kind") == "ng"
    ]
    assert len(ng_alarms) == 1                  # ng 알람 1회만
    assert tracker.current("CAMIDEM") == 1      # 연속 NG 카운터 1회만 증가
    assert len(_count_rows(client, auth, "LOTIDEMNG")) == 1


def test_different_inspected_at_creates_two_rows(client, auth, captured):
    t1 = datetime(2026, 7, 3, 9, 0, 0, 1000, tzinfo=timezone.utc).isoformat()
    t2 = datetime(2026, 7, 3, 9, 0, 0, 2000, tzinfo=timezone.utc).isoformat()
    r1 = client.post("/inspection", json=_insp(lot="LOTIDEM2", inspected_at=t1))
    r2 = client.post("/inspection", json=_insp(lot="LOTIDEM2", inspected_at=t2))
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["id"] != r2.json()["id"]
    assert len(_count_rows(client, auth, "LOTIDEM2")) == 2


def test_mes_staging_not_duplicated_on_resend(client, auth, captured):
    """재전송 시 mes_quality_if 도 1건 유지(기존 idem_key 로직)."""
    from db.base import SessionLocal
    from db.models import MesQualityIf

    payload = _insp(lot="LOTIDEMMES")
    client.post("/inspection", json=payload)
    client.post("/inspection", json=payload)

    db = SessionLocal()
    try:
        cnt = (
            db.query(MesQualityIf)
            .filter(MesQualityIf.lot == "LOTIDEMMES")
            .count()
        )
        assert cnt == 1
    finally:
        db.close()


def test_race_integrity_error_returns_existing_row(client, auth, captured, monkeypatch):
    """동시 재전송 레이스: 사전 조회가 miss 해도 ux_insp_natkey 충돌을 잡아
    기존 행을 반환한다(save_inspection IntegrityError 경로)."""
    import core.inspection_service as svc
    from aivis_types import InspectionResult
    from db.base import SessionLocal

    payload = _insp(lot="LOTIDEMRACE")
    r1 = client.post("/inspection", json=payload)
    assert r1.status_code == 201
    id1 = r1.json()["id"]

    # 사전 조회를 강제로 miss 시켜 INSERT → 유니크 충돌 경로 재현.
    real_find = svc.find_by_natural_key
    calls = {"n": 0}

    def flaky_find(db, result):
        calls["n"] += 1
        if calls["n"] == 1:
            return None  # 첫 호출(사전 조회)만 miss
        return real_find(db, result)

    monkeypatch.setattr(svc, "find_by_natural_key", flaky_find)

    db = SessionLocal()
    try:
        row, created = svc.save_inspection(
            db, InspectionResult(**payload), mes_mode="table"
        )
        assert created is False
        assert row.id == id1
    finally:
        db.close()
    assert calls["n"] >= 2  # IntegrityError 후 재조회 수행됨


def test_fk_integrity_error_still_backs_up_to_queue(client, auth, captured):
    """자연키 무관 IntegrityError(FK 위반)는 멱등 처리 대상 아님 → 로컬 큐 백업."""
    from core import local_queue

    before = local_queue.pending_count()
    r = client.post(
        "/inspection", json=_insp(lot="LOTIDEMFK", item_code="NO_SUCH_ITEM")
    )
    assert r.status_code == 201
    assert r.json()["status"] == "queued"
    assert local_queue.pending_count() == before + 1
