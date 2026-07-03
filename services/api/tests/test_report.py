"""월간 품질 리포트 생성 검증 (M12). PDF/XLSX 바이트 + 헤더 시그니처."""
from __future__ import annotations

from datetime import datetime, timezone


def _seed_item(client, auth):
    client.post("/master/items", headers=auth("qa1"), json={
        "item_code": "RPT", "item_name": "rpt", "ref_length_mm": 100.0,
        "tol_plus_mm": 0.5, "tol_minus_mm": 0.5, "px_to_mm_scale": 0.05,
    })


def _post(client, **over):
    base = {
        "lot": "R", "item_code": "RPT", "cam_id": "C",
        "inspected_at": datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc).isoformat(),
        "final_verdict": "OK", "defect_codes": [], "review_flag": False,
        "mes_synced": True, "proc_time_ms": 100,
    }
    base.update(over)
    r = client.post("/inspection", json=base)
    assert r.status_code == 201, r.text


def _seed_month(client, auth):
    _seed_item(client, auth)
    for i in range(5):
        _post(client, lot=f"OK{i}")
    _post(client, lot="NG0", final_verdict="NG", defect_codes=["LEN"], mes_synced=False)
    _post(client, lot="NG1", final_verdict="NG", defect_codes=["OIL", "SCR"],
          mes_synced=False,
          inspected_at=datetime(2026, 3, 11, 9, 0, tzinfo=timezone.utc).isoformat())


def test_report_pdf_signature(client, auth):
    _seed_month(client, auth)
    r = client.get("/kpi/report", headers=auth("qa1"),
                   params={"period": "2026-03", "fmt": "pdf"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    cd = r.headers["content-disposition"]
    assert "attachment" in cd
    assert 'filename="aivis_kpi_2026-03.pdf"' in cd
    # PDF 매직넘버 %PDF-
    assert r.content[:5] == b"%PDF-"
    assert len(r.content) > 500


def test_report_xlsx_signature(client, auth):
    _seed_month(client, auth)
    r = client.get("/kpi/report", headers=auth("qa1"),
                   params={"period": "2026-03", "fmt": "xlsx"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    cd = r.headers["content-disposition"]
    assert "attachment" in cd
    assert 'filename="aivis_kpi_2026-03.xlsx"' in cd
    # XLSX = zip 컨테이너 -> PK\x03\x04
    assert r.content[:4] == b"PK\x03\x04"
    assert len(r.content) > 500


def test_report_requires_quality(client, auth):
    # 작업자는 리포트 생성 불가(403).
    r = client.get("/kpi/report", headers=auth("op1"), params={"period": "2026-03"})
    assert r.status_code == 403


def test_report_default_period_is_current_month(client, auth):
    # period 미지정 -> 당월. 빈 달이어도 정상 생성(분모 0 보호).
    r = client.get("/kpi/report", headers=auth("qa1"), params={"fmt": "xlsx"})
    assert r.status_code == 200
    now = datetime.now(timezone.utc)
    fname = f"aivis_kpi_{now.year:04d}-{now.month:02d}.xlsx"
    assert fname in r.headers["content-disposition"]
    assert r.content[:4] == b"PK\x03\x04"


def test_report_no_auth_rejected(client):
    r = client.get("/kpi/report", params={"period": "2026-03"})
    assert r.status_code == 401
