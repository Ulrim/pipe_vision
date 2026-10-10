"""2026-10-10 개발 진척 점검(06 AI대전환) 지적 사항 보완 검증.

- MES: 주소가 없으면 가짜 전송이 자동으로 켜져 '연계 100%' 가 나오던 것.
- KPI: Claim·리드타임·공수 목표/연 누계/구축 전 기준값이 리포트에 없던 것.
- 목표값: 계획값(600ppm·30%)과 계약값(1,000ppm·40%)이 다르다는 지적 → 프로파일.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone

import pytest

from core import report as report_gen
from mes.adapter import MesAdapter
from mes.config import get_mes_config
from mes.transport import FakeMesTransport, UnconfiguredMesTransport


# ---- MES ---------------------------------------------------------------------

def test_rest_without_url_does_not_fake_success(monkeypatch):
    monkeypatch.setenv("MES_MODE", "rest")
    monkeypatch.delenv("MES_REST_URL", raising=False)
    monkeypatch.delenv("MES_REST_FAKE", raising=False)
    from core.config import get_settings
    get_settings.cache_clear()
    try:
        cfg = get_mes_config()
        assert cfg.effective_mode == "rest_unconfigured"
        a = MesAdapter(cfg)
        assert isinstance(a.transport, UnconfiguredMesTransport), "가짜 성공 금지"
    finally:
        get_settings.cache_clear()


def test_fake_transport_only_when_asked_and_labelled(monkeypatch):
    monkeypatch.setenv("MES_MODE", "rest")
    monkeypatch.delenv("MES_REST_URL", raising=False)
    monkeypatch.setenv("MES_REST_FAKE", "true")
    from core.config import get_settings
    get_settings.cache_clear()
    try:
        cfg = get_mes_config()
        assert cfg.effective_mode == "rest_fake"
        assert isinstance(MesAdapter(cfg).transport, FakeMesTransport)
    finally:
        get_settings.cache_clear()


def test_unconfigured_rest_keeps_rows_unsynced(monkeypatch):
    """주소가 없으면 연계 보류 — 행은 미연계로 남고 워치독이 나중에 보낸다."""
    from db.base import SessionLocal
    from db.models import Inspection, ItemMaster

    monkeypatch.setenv("MES_MODE", "rest")
    monkeypatch.delenv("MES_REST_URL", raising=False)
    monkeypatch.delenv("MES_REST_FAKE", raising=False)
    from core.config import get_settings
    get_settings.cache_clear()
    db = SessionLocal()
    try:
        if not db.get(ItemMaster, "RV"):
            db.add(ItemMaster(item_code="RV", item_name="rv", ref_length_mm=100,
                              tol_plus_mm=1, tol_minus_mm=1, px_to_mm_scale=0.1))
            db.commit()
        row = Inspection(lot="RV-1", item_code="RV", cam_id="RVCAM",
                         inspected_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
                         final_verdict="OK", defect_codes=[], mes_synced=False)
        db.add(row)
        db.commit()
        assert MesAdapter(get_mes_config()).sync_row(db, row) is False
        db.refresh(row)
        assert row.mes_synced is False
    finally:
        db.close()
        get_settings.cache_clear()


def test_summary_reports_mes_mode_and_consumed(client, auth):
    s = client.get("/kpi/summary", headers=auth("op1"), params={"period": "2026-05"}).json()
    assert s["mes_mode"] == "table"     # 테스트 env MES_MODE=table
    assert s["mes_consumed_count"] == 0  # MES 가 아직 아무것도 안 가져감


def test_fake_mes_rate_is_not_judged():
    from aivis_types import KpiSummary
    s = KpiSummary(period="2026-05", total_inspected=1, defect_count=0, process_defect_ppm=0,
                   auto_inspected=1, auto_inspection_rate_pct=100, misjudge_count=0,
                   miss_count=0, inspection_defect_rate_pct=0, stored_count=1,
                   mes_synced_count=1, storage_mes_rate_pct=100.0, mes_mode="rest_fake")
    res = {k: p for k, _ko, _la, _t, p in report_gen.evaluate_targets(s, [])}
    assert res["storage_mes_rate_pct"] is None, "가짜 전송 연계율로 합격을 찍으면 안 된다"
    assert "가짜 전송" in report_gen.target_actual_text("storage_mes_rate_pct", s, [])


# ---- 목표 프로파일 · 구축 전 기준값 ----------------------------------------------

def _targets(monkeypatch=None):
    return {k: (t, r) for k, _ko, _la, t, r in report_gen.kpi_targets()}


def test_plan_profile_is_default_and_has_manual_kpis(monkeypatch):
    monkeypatch.delenv("AIVIS_KPI_PROFILE", raising=False)
    t = _targets()
    assert t["process_defect_ppm"][1] == "lte:600.0"
    assert t["inspection_defect_rate_pct"][1] == "lte:30.0"
    assert t["claim_count_ytd"][1] == "lte:2.0"
    assert t["lead_time_days"][1] == "lte:5.0"
    assert t["workload_index"][1] == "lte:50.0"


def test_contract_profile_switches_with_one_env(monkeypatch):
    monkeypatch.setenv("AIVIS_KPI_PROFILE", "contract")
    t = _targets()
    assert t["inspection_defect_rate_pct"][1] == "lte:40.0"
    assert t["claim_count_ytd"][1] == "lte:3.0"
    assert t["lead_time_days"][1] == "lte:6.0"
    assert t["shipment_leak_ppm"][1] == "lte:1000.0"
    # 개별 env 가 프로파일 위에 덮인다.
    monkeypatch.setenv("AIVIS_KPI_TARGET_LEAD_DAYS", "5.5")
    assert _targets()["lead_time_days"][1] == "lte:5.5"


def test_baselines_default_to_plan_before_values(monkeypatch):
    for k in ("PROCESS_PPM", "LEAK_PPM", "CLAIM", "LEAD_DAYS", "WORKLOAD"):
        monkeypatch.delenv(f"AIVIS_KPI_BASELINE_{k}", raising=False)
    assert report_gen.kpi_baseline("process_defect_ppm") == 2000.0
    assert report_gen.kpi_baseline("claim_count_ytd") == 5.0
    assert report_gen.kpi_baseline("lead_time_days") == 7.0
    assert report_gen.kpi_baseline("workload_index") == 100.0
    assert report_gen.kpi_baseline("inspection_defect_rate_pct") is None
    monkeypatch.setenv("AIVIS_KPI_BASELINE_PROCESS_PPM", "2350")
    assert report_gen.kpi_baseline("process_defect_ppm") == 2350.0


def test_targets_endpoint_carries_baseline_and_profile(client, auth, monkeypatch):
    monkeypatch.delenv("AIVIS_KPI_PROFILE", raising=False)
    rows = {t["key"]: t for t in client.get("/kpi/targets", headers=auth("op1")).json()}
    assert rows["process_defect_ppm"]["baseline_value"] == 2000.0
    assert rows["claim_count_ytd"]["direction"] == "lower"
    assert rows["lead_time_days"]["profile"] == "plan"


# ---- Claim 연 누계 · 리포트 -------------------------------------------------------

def test_claim_ytd_sums_jan_to_this_month(client, auth):
    for month, n in ((1, 1), (3, 2), (7, 4)):
        r = client.post("/kpi/manual", headers=auth("qa1"), json={
            "period": f"2029-{month:02d}-01", "claim_count": n, "lead_time_days": 6.0,
            "workload_index": 60.0,
        })
        assert r.status_code == 200
    s = client.get("/kpi/summary", headers=auth("op1"), params={"period": "2029-03"}).json()
    assert s["claim_count_ytd"] == 3, "1월 1건 + 3월 2건(7월은 아직)"
    s7 = client.get("/kpi/summary", headers=auth("op1"), params={"period": "2029-07"}).json()
    assert s7["claim_count_ytd"] == 7
    none = client.get("/kpi/summary", headers=auth("op1"), params={"period": "2028-06"}).json()
    assert none["claim_count_ytd"] is None, "입력이 없으면 0 이 아니라 모름"


def test_preview_targets_have_before_column_and_manual_rows(client, auth, monkeypatch):
    monkeypatch.delenv("AIVIS_KPI_PROFILE", raising=False)
    p = client.get("/kpi/report/preview", headers=auth("op1"),
                   params={"period": "2029-07"}).json()
    t = {x["key"]: x for x in p["targets"]}
    assert t["process_defect_ppm"]["baseline"] == "2000"
    assert t["claim_count_ytd"]["actual"] == "7"
    assert t["claim_count_ytd"]["achieved"] is False   # 7 > 2
    assert t["lead_time_days"]["baseline"] == "7"
    assert t["inspection_defect_rate_pct"]["baseline"] == "-"


def test_xlsx_report_contains_split_and_manual_kpis(client, auth):
    openpyxl = pytest.importorskip("openpyxl")
    r = client.get("/kpi/report", headers=auth("qa1"), params={"period": "2029-07", "fmt": "xlsx"})
    assert r.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    text = "\n".join(str(c.value) for row in wb.active.iter_rows() for c in row if c.value is not None)
    for needle in ("오검 (AI NG → 재확인 OK)", "미검 (AI OK → 재확인 NG)", "재확인 대기",
                   "MES 연계 방식", "Claim 연 누계", "구축 전", "목표 기준"):
        assert needle in text, needle


def test_pdf_report_still_renders(client, auth):
    r = client.get("/kpi/report", headers=auth("qa1"), params={"period": "2029-07", "fmt": "pdf"})
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
