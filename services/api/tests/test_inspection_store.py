"""검사결과 저장/조회 + 로컬 큐 백업 핵심 경로 (M7,M8)."""
from __future__ import annotations

from datetime import datetime, timezone


def _insp(**over):
    base = {
        "lot": "LOT001",
        "item_code": "HP12",
        "cam_id": "CAM1",
        "inspected_at": datetime(2026, 6, 10, 8, 0, tzinfo=timezone.utc).isoformat(),
        "final_verdict": "OK",
        "defect_codes": [],
        "review_flag": False,
        "mes_synced": False,
        "proc_time_ms": 120,
    }
    base.update(over)
    return base


def _make_item(client, auth):
    client.post(
        "/master/items",
        headers=auth("qa1"),
        json={
            "item_code": "HP12",
            "item_name": "Header Pipe 12",
            "ref_length_mm": 250.0,
            "tol_plus_mm": 0.5,
            "tol_minus_mm": 0.5,
            "px_to_mm_scale": 0.05,
        },
    )


def test_store_and_query(client, auth):
    _make_item(client, auth)
    r = client.post("/inspection", json=_insp())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "stored"
    assert body["id"] > 0

    # 필터 조회: lot (조회는 로그인 필요 operator+)
    r = client.get("/inspection", headers=auth("op1"), params={"lot": "LOT001"})
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) >= 1
    assert rows[0]["lot"] == "LOT001"
    assert rows[0]["item_code"] == "HP12"


def test_query_filters(client, auth):
    _make_item(client, auth)
    client.post("/inspection", json=_insp(lot="LOTNG", final_verdict="NG",
                                          defect_codes=["LEN"]))
    # verdict 필터 (조회는 로그인 필요)
    r = client.get("/inspection", headers=auth("op1"),
                   params={"verdict": "NG", "lot": "LOTNG"})
    assert r.status_code == 200
    rows = r.json()
    assert all(x["final_verdict"] == "NG" for x in rows)
    assert any("LEN" in x["defect_codes"] for x in rows)


def test_images_and_review(client, auth):
    _make_item(client, auth)
    r = client.post(
        "/inspection",
        json=_insp(
            lot="LOTIMG",
            final_verdict="NG",
            defect_codes=["SCR"],
            review_flag=True,
            raw_image_path="raw/x.jpg",
            result_image_path="result/x.jpg",
        ),
    )
    insp_id = r.json()["id"]

    img = client.get(f"/inspection/{insp_id}/images", headers=auth("op1"))
    assert img.status_code == 200
    assert img.json()["raw_image_path"] == "raw/x.jpg"

    # 재확인(작업자 권한)
    rev = client.patch(
        f"/inspection/{insp_id}/review",
        headers=auth("op1"),
        json={"manual_verdict": "OK", "operator": "op1"},
    )
    assert rev.status_code == 200, rev.text
    assert rev.json()["manual_verdict"] == "OK"
    assert rev.json()["review_flag"] is False


def test_mes_staging_idempotent(client, auth):
    """table 모드: 저장 시 mes_quality_if 멱등 스테이징(중복 적재 방지)."""
    _make_item(client, auth)
    payload = _insp(lot="LOTMES", cam_id="CAMZ")
    r1 = client.post("/inspection", json=payload)
    assert r1.status_code == 201
    # 동일 멱등키 재전송(REST) -> duplicate
    r2 = client.post("/mes/quality", json=payload)
    assert r2.json()["status"] == "duplicate"


def test_local_queue_backup_on_db_failure(client, auth, monkeypatch):
    """DB 저장 실패 시 로컬 큐 백업 + status=queued (M7 DoD)."""
    import routers.inspection as insp_mod
    from core import local_queue

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(insp_mod, "save_inspection", boom)
    before = local_queue.pending_count()
    r = client.post("/inspection", json=_insp(lot="LOTQ"))
    assert r.status_code == 201
    assert r.json()["status"] == "queued"
    assert local_queue.pending_count() == before + 1


def test_double_failure_db_and_local_queue_returns_5xx_not_queued(monkeypatch, caplog):
    """DB 저장도 로컬 큐 백업도 실패(디스크 동시 소진 등)하면 검사결과가

    어디에도 남지 않는다. 이때 status=queued(200대)로 응답하면 엣지 워커가
    "성공"으로 오인해 자기 오프라인 스풀에도 적재하지 않아 완전 유실된다
    (M7 DoD 100% 저장 위반). 반드시 5xx 를 돌려줘 엣지 스풀이 최후 방어선으로
    재시도하게 해야 하고, DB/파일에 의존하지 않는 표준 로거로 흔적을 남겨야
    한다.
    """
    import logging

    from fastapi.testclient import TestClient

    import routers.inspection as insp_mod
    from core import local_queue
    from main import app

    def db_boom(*a, **k):
        raise RuntimeError("db down")

    def backup_boom(*a, **k):
        raise OSError("[Errno 28] No space left on device")

    monkeypatch.setattr(insp_mod, "save_inspection", db_boom)
    monkeypatch.setattr(local_queue, "backup", backup_boom)
    before = local_queue.pending_count()

    # 기본 client 픽스처는 디버깅 편의를 위해 서버 예외를 테스트로 그대로
    # 재던진다(raise_server_exceptions=True, TestClient 기본값). 실제 클라이언트
    # (엣지 워커)가 받는 진짜 HTTP 응답(5xx)을 검증하려면 이를 꺼야 한다.
    with TestClient(app, raise_server_exceptions=False) as strict_client:
        with caplog.at_level(logging.CRITICAL, logger="aivis.api.inspection"):
            r = strict_client.post("/inspection", json=_insp(lot="LOTDOUBLEFAIL"))

    assert r.status_code >= 500, r.text
    # 기본 500 응답은 JSON 이 아니다(커스텀 에러 핸들러 없음) — "queued" 로
    # 오인될 만한 성공 표시가 전혀 없는지만 확인.
    assert "queued" not in r.text
    # 백업 자체가 실패했으므로 큐에도 안 늘어나야 한다(거짓 "적재됨" 방지).
    assert local_queue.pending_count() == before
    assert any(
        "완전 유실" in rec.message and "LOTDOUBLEFAIL" in rec.message
        for rec in caplog.records
    )
