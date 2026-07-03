"""SAT 자동 검증 하니스 — §1.2 4지표(실생산 모사) + 사용성 스모크.

FAT 와 동일 골격이되 더 큰 표본/혼합 LOT/교대 메타로 종합 검증한다.
실데이터 디렉터리(AIVIS_DATASET_DIR)가 있으면 우선 사용, 없으면 합성으로 모사.

리포트: tests/sat/report/sat_metrics.json + sat_metrics.md.
지표 미달 시 FAIL 차단.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.harness import dataset as ds
from tests.harness import metrics as mt
from tests.harness import runner
from tests.harness.report import write_reports

# SAT 규모: 합성 모사 시 클래스별 80장(×7 = 560장). 처리속도 1,000장 배치.
_PER_CLASS = 80
_LATENCY_N = 1000
_LOTS = ("LOT-A", "LOT-B", "LOT-C")
_SHIFTS = ("DAY", "SWING", "NIGHT")
_REPORT_DIR = Path(__file__).resolve().parent / "report"


@pytest.fixture(scope="module")
def sat_env(tmp_path_factory):
    item = mt.make_item_master()

    # 실데이터 우선(부록 A.6 AIVIS_DATASET_DIR), 없으면 합성 모사.
    real_dir = os.getenv("AIVIS_DATASET_DIR")
    if real_dir and Path(real_dir).exists():
        data_dir = Path(real_dir)
        source = f"real dataset (AIVIS_DATASET_DIR={real_dir})"
    else:
        data_dir = tmp_path_factory.mktemp("sat_dataset")
        ds.write_groundtruth_dataset(data_dir, per_class=_PER_CLASS, item_code=item.item_code)
        source = f"synthetic 실생산 모사 (per_class={_PER_CLASS}, 혼합 LOT/교대)"

    gt = runner.load_groundtruth(data_dir, view="SIDE")
    assert gt, f"정답셋 비었음: {data_dir}"

    core = runner.run_pipeline_over(gt, item)
    latency = runner.latency_batch(gt, item, n=_LATENCY_N)
    storage = runner.verify_storage_and_mes(
        core.runs, item, lots=_LOTS, shifts=_SHIFTS, cam_id="CAM-SAT"
    )
    return {
        "item": item, "gt": gt, "core": core,
        "latency": latency, "storage": storage, "source": source,
    }


@pytest.fixture(scope="module")
def sat_usability(sat_env):
    """사용성 스모크: 로그인 → 검사이력 조회 → KPI 요약 산출(operator 권한)."""
    from fastapi.testclient import TestClient
    from main import app
    from db.base import SessionLocal, init_db
    from core.security import hash_password
    from db.models import AppUser

    init_db()
    # operator 계정 보장(시드 OFF 환경이라 직접 생성).
    db = SessionLocal()
    try:
        if not db.get(AppUser, "sat_op"):
            db.add(AppUser(username="sat_op", pw_hash=hash_password("pw12345"),
                           role="operator", active=True))
            db.commit()
    finally:
        db.close()

    out = {}
    with TestClient(app) as client:
        r = client.post("/auth/login", json={"username": "sat_op", "password": "pw12345"})
        assert r.status_code == 200, r.text
        tok = r.json()["access_token"]
        h = {"Authorization": f"Bearer {tok}"}

        # 검사이력 조회(operator+ 가드 통과 확인).
        rl = client.get("/inspection", headers=h, params={"limit": 5})
        out["list_status"] = rl.status_code
        out["list_count"] = len(rl.json()) if rl.status_code == 200 else 0

        # KPI 요약(당월) — 산출식 동작 스모크.
        rk = client.get("/kpi/summary", headers=h, params={"period": "2026-06"})
        out["kpi_status"] = rk.status_code
        out["kpi"] = rk.json() if rk.status_code == 200 else None
    return out


@pytest.fixture(scope="module")
def sat_payload(sat_env, sat_usability):
    core = sat_env["core"]
    latency = sat_env["latency"]
    storage = sat_env["storage"]
    acc = core.item_accuracy

    usability_ok = (
        sat_usability["list_status"] == 200 and sat_usability["kpi_status"] == 200
    )

    kpi_table = [
        {"no": 1, "name": "자동검사율",
         "measured": f"{core.auto_rate_pct:.3f}%", "target": "100%",
         "passed": core.auto_rate_pct >= mt.AUTO_RATE_MIN},
        {"no": 2, "name": "항목별 판정 정확도(최저)",
         "measured": f"{acc.min_accuracy_pct:.3f}%", "target": "≥95%",
         "passed": acc.passed},
        {"no": 3, "name": "검사 처리속도 p95",
         "measured": f"{latency.p95_ms:.3f}ms", "target": "≤300ms",
         "passed": latency.passed},
        {"no": 4, "name": "데이터 저장 & MES 연계율",
         "measured": f"저장 {storage.storage_rate_pct:.3f}% / 연계 {storage.mes_rate_pct:.3f}%",
         "target": "100%", "passed": storage.passed},
        {"no": 5, "name": "사용성(로그인→조회→KPI)",
         "measured": "OK" if usability_ok else "FAIL",
         "target": "동작", "passed": usability_ok},
    ]
    overall = all(r["passed"] for r in kpi_table)

    payload = {
        "title": "AIVIS SAT 결과서 (실생산 모사, §1.2 + 사용성)",
        "dataset_source": sat_env["source"],
        "sample_count": core.sample_count,
        "overall_passed": overall,
        "kpi_table": kpi_table,
        "metric1_auto_inspection": {
            "sample_count": core.sample_count,
            "completed_count": core.completed_count,
            "auto_rate_pct": round(core.auto_rate_pct, 4),
            "passed": core.auto_rate_pct >= mt.AUTO_RATE_MIN,
        },
        "metric2_item_accuracy": acc.as_dict(),
        "metric3_latency": latency.as_dict(),
        "metric4_storage_mes": storage.as_dict(),
        "usability_smoke": sat_usability,
        "lot_mix": list(_LOTS),
        "shift_mix": list(_SHIFTS),
        "notes": [
            "혼합 LOT/교대 메타로 적재 — 실생산 모사.",
            "AIVIS_DATASET_DIR 설정 시 실데이터를 우선 사용(부록 A.6).",
            "사용성: operator 로그인→/inspection 조회→/kpi/summary 산출 스모크.",
        ],
    }
    paths = write_reports(_REPORT_DIR, "sat_metrics", payload)
    payload["_report_paths"] = paths
    return payload


def test_sat_metric1_auto_inspection_rate_100(sat_env, sat_payload):
    core = sat_env["core"]
    assert core.auto_rate_pct >= mt.AUTO_RATE_MIN, (
        f"SAT 자동검사율 {core.auto_rate_pct:.3f}% < 100%"
    )


def test_sat_metric2_item_accuracy_min_95(sat_env, sat_payload):
    acc = sat_env["core"].item_accuracy
    detail = {k: round(v.accuracy_pct, 3) for k, v in acc.per_item.items()}
    assert acc.passed, f"SAT 항목 정확도 최저 {acc.min_accuracy_pct:.3f}% < 95% — {detail}"


def test_sat_metric3_latency_p95_under_300(sat_env, sat_payload):
    lat = sat_env["latency"]
    assert lat.passed, f"SAT p95 {lat.p95_ms:.3f}ms > 300ms"


def test_sat_metric4_storage_and_mes_rate_100(sat_env, sat_payload):
    st = sat_env["storage"]
    assert st.storage_rate_pct >= mt.STORAGE_MES_RATE_MIN, f"SAT 저장율 {st.storage_rate_pct:.3f}%"
    assert st.mes_rate_pct >= mt.STORAGE_MES_RATE_MIN, f"SAT 연계율 {st.mes_rate_pct:.3f}%"


def test_sat_usability_smoke(sat_usability, sat_payload):
    assert sat_usability["list_status"] == 200
    assert sat_usability["kpi_status"] == 200


def test_sat_reports_written(sat_payload):
    paths = sat_payload["_report_paths"]
    assert Path(paths["json"]).exists() and Path(paths["md"]).exists()
    assert sat_payload["overall_passed"], "SAT 종합 판정 FAIL — 리포트 참조"
