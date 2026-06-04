"""KPI 산출식 검증 (CLAUDE.md §1.1). 임의 변형 금지 — 수치 정확 검증."""
from __future__ import annotations

from datetime import datetime, timezone


def _post(client, **over):
    base = {
        "lot": "K",
        "item_code": "KPI",
        "cam_id": "C",
        "inspected_at": datetime(2026, 5, 15, 9, 0, tzinfo=timezone.utc).isoformat(),
        "final_verdict": "OK",
        "defect_codes": [],
        "review_flag": False,
        "mes_synced": False,
        "proc_time_ms": 100,
    }
    base.update(over)
    r = client.post("/inspection", json=base)
    assert r.status_code == 201, r.text
    return r.json()


def _ensure_kpi_item(client, auth):
    client.post("/master/items", headers=auth("qa1"), json={
        "item_code": "KPI", "item_name": "kpi", "ref_length_mm": 100.0,
        "tol_plus_mm": 0.5, "tol_minus_mm": 0.5, "px_to_mm_scale": 0.05,
    })


def test_kpi_formulas(client, auth):
    """2026-05 월에 통제된 데이터를 넣고 §1.1 산출식을 정확 검증.

    구성: 총 10건.
      - NG 2건(공정 불량) -> ppm = 2/10*1e6 = 200000
      - mes_synced 7건    -> 저장&연계율 = 7/10*100 = 70
      - 오검 1건(manual!=final), 미검 0건
        -> 검사불량률 = (1+0)/10*100 = 10
      - 자동검사율: 모두 final_verdict 존재 -> 10/10*100 = 100
    """
    _ensure_kpi_item(client, auth)
    # 8 OK, 그중 7 synced
    for i in range(8):
        _post(client, lot=f"OK{i}", mes_synced=(i < 7))
    # 2 NG
    _post(client, lot="NG0", final_verdict="NG", defect_codes=["LEN"], mes_synced=False)
    _post(client, lot="NG1", final_verdict="NG", defect_codes=["OIL"], mes_synced=False)

    # 오검 1건: AI=OK, manual=NG (위 OK0 를 재확인 처리로 만들기 위해 새 행 추가 대신
    # 별도 행 1건 더 넣고 재확인). -> 총 건수를 10으로 유지하려고 위 10건만 사용:
    # 오검 1건: OK0 을 재확인 처리해 AI=OK, manual=NG 로 만든다(총건수 10 유지).
    rows = client.get("/inspection", params={"lot": "OK0"}).json()
    ok0 = rows[0]["id"]
    client.patch(f"/inspection/{ok0}/review", headers=auth("qa1"),
                 json={"manual_verdict": "NG", "review_flag": False})

    summary = client.get("/kpi/summary", params={"period": "2026-05"}).json()
    total = summary["total_inspected"]
    # 실제 적재된 총건수 기준으로 산출식 일관성 검증.
    assert total == 10
    assert summary["defect_count"] == 2
    assert summary["process_defect_ppm"] == 2 / 10 * 1_000_000
    assert summary["auto_inspected"] == 10
    assert summary["auto_inspection_rate_pct"] == 100.0
    assert summary["mes_synced_count"] == 7
    assert summary["storage_mes_rate_pct"] == 7 / 10 * 100
    # 오검 1(OK0->manual NG), 미검 0(미검 행 없음) -> 검사불량률 = 1/10*100 = 10
    assert summary["misjudge_count"] == 1
    assert summary["miss_count"] == 0
    assert summary["inspection_defect_rate_pct"] == 1 / 10 * 100
    assert summary["avg_proc_time_ms"] == 100.0


def test_kpi_miss_count(client, auth):
    """미검(review_flag=True & manual None) 카운트 검증. 다른 월(2026-04)."""
    _ensure_kpi_item(client, auth)
    when = datetime(2026, 4, 2, 9, 0, tzinfo=timezone.utc).isoformat()
    # review_flag True, manual 미입력 = 미검 1건
    r = client.post("/inspection", json={
        "lot": "M", "item_code": "KPI", "cam_id": "C", "inspected_at": when,
        "final_verdict": "NG", "defect_codes": ["SCR"], "review_flag": True,
        "mes_synced": False, "proc_time_ms": 90,
    })
    assert r.status_code == 201, r.text
    s = client.get("/kpi/summary", params={"period": "2026-04"}).json()
    assert s["total_inspected"] == 1
    assert s["miss_count"] == 1
    assert s["inspection_defect_rate_pct"] == 1 / 1 * 100


def test_kpi_empty_period_no_divzero(client):
    s = client.get("/kpi/summary", params={"period": "2030-01"}).json()
    assert s["total_inspected"] == 0
    assert s["process_defect_ppm"] == 0.0
    assert s["storage_mes_rate_pct"] == 0.0
    assert s["avg_proc_time_ms"] is None


def test_kpi_manual_upsert(client, auth):
    r = client.post("/kpi/manual", headers=auth("qa1"), json={
        "period": "2026-05-01", "claim_count": 3, "workload_index": 75.0,
        "lead_time_days": 5.0, "note": "x",
    })
    assert r.status_code == 200
    assert r.json()["claim_count"] == 3
    # summary 에 수기값 노출
    s = client.get("/kpi/summary", params={"period": "2026-05"}).json()
    assert s["claim_count"] == 3
    assert s["lead_time_days"] == 5.0


def test_kpi_manual_requires_quality(client, auth):
    r = client.post("/kpi/manual", headers=auth("op1"), json={"period": "2026-07-01"})
    assert r.status_code == 403
