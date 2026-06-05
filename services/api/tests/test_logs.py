"""로그 적재/조회 (M15)."""
from __future__ import annotations

from datetime import datetime, timezone


def test_login_writes_user_log_and_filter(client, auth):
    # 로그인은 user 카테고리 로그를 남긴다(conftest 외 추가 로그인).
    client.post("/auth/login", json={"username": "qa1", "password": "pw12345"})
    r = client.get("/logs", headers=auth("qa1"), params={"category": "user"})
    assert r.status_code == 200
    rows = r.json()
    assert all(x["category"] == "user" for x in rows)
    assert any("login" in (x["message"] or "") for x in rows)


def test_logs_requires_quality(client, auth):
    assert client.get("/logs", headers=auth("op1")).status_code == 403


def test_inspection_store_writes_db_log(client, auth):
    """검사결과 저장 성공 시 db 카테고리 로그 적재(M15)."""
    client.post("/master/items", headers=auth("qa1"), json={
        "item_code": "LOGIT", "item_name": "log", "ref_length_mm": 100.0,
        "tol_plus_mm": 0.5, "tol_minus_mm": 0.5, "px_to_mm_scale": 0.05,
    })
    r = client.post("/inspection", json={
        "lot": "LOGLOT", "item_code": "LOGIT", "cam_id": "C",
        "inspected_at": datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc).isoformat(),
        "final_verdict": "OK", "defect_codes": [], "review_flag": False,
        "mes_synced": False, "proc_time_ms": 100,
    })
    assert r.status_code == 201, r.text
    logs = client.get("/logs", headers=auth("qa1"), params={"category": "db"}).json()
    assert any("inspection.store ok" in (x["message"] or "") for x in logs)


def test_inspection_store_failure_writes_error_log(client, auth, monkeypatch):
    """저장 실패 시 error 카테고리 로그 + 로컬 큐 백업(M15/M7)."""
    import routers.inspection as insp_mod

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(insp_mod, "save_inspection", boom)
    r = client.post("/inspection", json={
        "lot": "FAILLOT", "item_code": "LOGIT", "cam_id": "C",
        "inspected_at": datetime(2026, 8, 2, 9, 0, tzinfo=timezone.utc).isoformat(),
        "final_verdict": "NG", "defect_codes": ["LEN"], "review_flag": False,
        "mes_synced": False, "proc_time_ms": 100,
    })
    assert r.status_code == 201
    assert r.json()["status"] == "queued"
    logs = client.get("/logs", headers=auth("qa1"), params={"category": "error"}).json()
    assert any("inspection.store fail" in (x["message"] or "") for x in logs)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["db"] == "up"
