"""GET /inspection/stats — 통계 화면 서버 집계(2026-10-10 점검 보완).

통계 화면이 5,000건을 요청했는데 목록 API 상한이 2,000건이라 요청이 실패했다.
이제 DB 가 전 건을 센다 — 표본이 아니다.
"""
from __future__ import annotations

from datetime import datetime, timezone


def _post(client, i, **over):
    body = {
        "lot": f"ST-{i}", "item_code": "STAT", "cam_id": over.pop("cam_id", "ST1"),
        "inspected_at": over.pop("at"), "final_verdict": "OK", "defect_codes": [],
        "review_flag": False, "mes_synced": False, "proc_time_ms": 80,
    }
    body.update(over)
    r = client.post("/inspection", json=body)
    assert r.status_code == 201, r.text


def test_stats_counts_everything_by_code_and_kst_month(client, auth):
    client.post("/master/items", headers=auth("qa1"), json={
        "item_code": "STAT", "item_name": "s", "ref_length_mm": 100.0,
        "tol_plus_mm": 0.5, "tol_minus_mm": 0.5, "px_to_mm_scale": 0.05,
    })
    # 2031-01-31 23:30 KST = 2031-01-31 14:30 UTC → 1월
    # 2031-01-31 15:30 UTC = 2031-02-01 00:30 KST → 2월 (UTC 로 자르면 1월로 틀린다)
    _post(client, 1, at=datetime(2031, 1, 31, 14, 30, tzinfo=timezone.utc).isoformat())
    _post(client, 2, at=datetime(2031, 1, 31, 15, 30, tzinfo=timezone.utc).isoformat(),
          final_verdict="NG", defect_codes=["LEN"])
    _post(client, 3, at=datetime(2031, 2, 2, tzinfo=timezone.utc).isoformat(),
          final_verdict="NG", defect_codes=["OIL", "DIS", "MULTI"])
    _post(client, 4, at=datetime(2031, 2, 3, tzinfo=timezone.utc).isoformat(), cam_id="ST2",
          final_verdict="NG", defect_codes=["LEN"])

    params = {"item": "STAT", "from": "2031-01-01T00:00:00Z", "to": "2031-03-01T00:00:00Z"}
    r = client.get("/inspection/stats", headers=auth("op1"), params=params)
    assert r.status_code == 200, r.text
    s = r.json()
    assert (s["total"], s["ng"]) == (4, 3)
    codes = {c["code"]: c["count"] for c in s["by_code"]}
    assert codes == {"LEN": 2, "OIL": 1, "DIS": 1, "MULTI": 1}
    months = {m["month"]: (m["total"], m["ng"]) for m in s["monthly"]}
    assert months == {"2031-01": (1, 0), "2031-02": (3, 3)}, "월 경계는 한국 시각"
    feb = next(m for m in s["monthly"] if m["month"] == "2031-02")
    assert feb["defect_rate_pct"] == 100.0

    only2 = client.get("/inspection/stats", headers=auth("op1"),
                       params={**params, "cam_id": "ST2"}).json()
    assert only2["total"] == 1


def test_stats_requires_login_and_does_not_collide_with_id_route(client, auth):
    assert client.get("/inspection/stats").status_code == 401
    r = client.get("/inspection/stats", headers=auth("op1"), params={"item": "NOPE"})
    assert r.status_code == 200 and r.json()["total"] == 0
