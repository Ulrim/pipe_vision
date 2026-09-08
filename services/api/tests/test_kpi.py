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
    rows = client.get("/inspection", headers=auth("op1"), params={"lot": "OK0"}).json()
    ok0 = rows[0]["id"]
    client.patch(f"/inspection/{ok0}/review", headers=auth("qa1"),
                 json={"manual_verdict": "NG", "review_flag": False})

    summary = client.get("/kpi/summary", headers=auth("op1"),
                         params={"period": "2026-05"}).json()
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
    s = client.get("/kpi/summary", headers=auth("op1"),
                   params={"period": "2026-04"}).json()
    assert s["total_inspected"] == 1
    assert s["miss_count"] == 1
    assert s["inspection_defect_rate_pct"] == 1 / 1 * 100


def test_kpi_empty_period_no_divzero(client, auth):
    s = client.get("/kpi/summary", headers=auth("op1"),
                   params={"period": "2030-01"}).json()
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
    s = client.get("/kpi/summary", headers=auth("op1"),
                   params={"period": "2026-05"}).json()
    assert s["claim_count"] == 3
    assert s["lead_time_days"] == 5.0


def test_kpi_manual_requires_quality(client, auth):
    r = client.post("/kpi/manual", headers=auth("op1"), json={"period": "2026-07-01"})
    assert r.status_code == 403


# --- 출하유출불량률(계약 성과지표) -------------------------------------------
#
# 계약 성과지표는 공정불량률이 아니라 **출하유출불량률**이다. 두 지표는 분자도
# 분모도 다르다(걸러낸 불량 ÷ 검사수량 vs 유출된 부적합 ÷ 출하수량). 리포트가
# 한쪽 잣대로만 합격을 찍으면 인수 심사에서 기준이 어긋나므로 둘 다 산출한다.


def test_shipment_leak_ppm_from_manual_input(client, auth):
    """수기 입력(출하수량/유출 부적합수량)으로 출하유출불량률을 산출한다."""
    r = client.post("/kpi/manual", headers=auth("qa1"), json={
        "period": "2026-07-01",
        "shipped_qty": 200_000,
        "leak_defect_qty": 150,
    })
    assert r.status_code == 200, r.text
    assert r.json()["shipped_qty"] == 200_000

    s = client.get("/kpi/summary?period=2026-07", headers=auth("op1")).json()
    # 150 / 200000 × 1e6 = 750 ppm
    assert s["shipment_leak_ppm"] == 750.0
    assert s["leak_defect_qty"] == 150


def test_shipment_leak_ppm_is_none_without_input(client, auth):
    """수기 입력이 없으면 None. 0 으로 채우면 '유출 없음(합격)'으로 오독된다."""
    s = client.get("/kpi/summary?period=2026-11", headers=auth("op1")).json()
    assert s["shipment_leak_ppm"] is None
    assert s["shipped_qty"] is None


def test_shipment_leak_ppm_zero_shipped_is_none(client, auth):
    """출하수량 0 이면 산출 불가 — 0 으로 나누지 않고 None."""
    client.post("/kpi/manual", headers=auth("qa1"), json={
        "period": "2026-12-01", "shipped_qty": 0, "leak_defect_qty": 3,
    })
    s = client.get("/kpi/summary?period=2026-12", headers=auth("op1")).json()
    assert s["shipment_leak_ppm"] is None


def test_leak_target_pending_when_no_manual_input(client, auth, monkeypatch):
    """유출 실적이 없으면 달성 판정을 '보류'로 낸다(합격으로 찍지 않는다)."""
    from core import report as report_gen

    s = client.get("/kpi/summary?period=2026-11", headers=auth("op1")).json()
    from aivis_types import KpiSummary

    targets = report_gen.evaluate_targets(KpiSummary(**s), [])
    leak = [t for t in targets if t[0] == "shipment_leak_ppm"]
    assert leak and leak[0][4] is None, "실적 없음은 합격/불합격이 아니라 보류"


def test_kpi_targets_configurable_by_env(monkeypatch):
    """목표치를 env 로 바꿀 수 있다(현장 재협의 시 코드 수정 없이 반영)."""
    from core import report as report_gen

    monkeypatch.setenv("AIVIS_KPI_TARGET_PROCESS_PPM", "1000")
    monkeypatch.setenv("AIVIS_KPI_TARGET_INSPECTION_PCT", "40")
    rules = {k: rule for k, _ko, _en, _txt, rule in report_gen.kpi_targets()}
    assert rules["process_defect_ppm"] == "lte:1000.0"
    assert rules["inspection_defect_rate_pct"] == "lte:40.0"

    monkeypatch.delenv("AIVIS_KPI_TARGET_PROCESS_PPM")
    monkeypatch.setenv("AIVIS_KPI_TARGET_PROCESS_PPM", "not-a-number")
    rules = {k: rule for k, _ko, _en, _txt, rule in report_gen.kpi_targets()}
    assert rules["process_defect_ppm"] == "lte:600.0", "잘못된 값이면 기본값"
