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
