"""FAT 자동 검증 하니스 — §1.2 4개 인수지표(샘플 기반).

자립 실행: 합성 정답셋(부록 A.4/A.5) + 폴백 CV + sqlite backend.
지표 미달 시 pytest FAIL 로 차단(자동검사율<100, 정확도<95, p95>300, 저장·연계율<100).

리포트: tests/fat/report/fat_metrics.json + fat_metrics.md (§9 qa 원칙).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.harness import dataset as ds
from tests.harness import metrics as mt
from tests.harness import runner
from tests.harness.report import write_reports

# FAT 데이터셋 규모: 클래스별 충분히(각 40장 × 7클래스 = 280장).
_PER_CLASS = 40
_LATENCY_N = 1000
_REPORT_DIR = Path(__file__).resolve().parent / "report"


@pytest.fixture(scope="module")
def fat_env(tmp_path_factory):
    """정답셋 생성 + 파이프라인 전건 실행(지표1/2/3 입력) + backend 적재(지표4)."""
    item = mt.make_item_master()
    data_dir = tmp_path_factory.mktemp("fat_dataset")
    ds.write_groundtruth_dataset(data_dir, per_class=_PER_CLASS, item_code=item.item_code)

    gt = runner.load_groundtruth(data_dir, view="SIDE")
    assert gt, "정답셋이 비었습니다 — 데이터 생성 실패"

    core = runner.run_pipeline_over(gt, item)
    latency = runner.latency_batch(gt, item, n=_LATENCY_N)
    storage = runner.verify_storage_and_mes(core.runs, item)

    return {
        "item": item,
        "gt": gt,
        "core": core,
        "latency": latency,
        "storage": storage,
    }


@pytest.fixture(scope="module")
def fat_payload(fat_env):
    """4지표를 종합한 리포트 payload 를 구성하고 디스크에 기록."""
    core = fat_env["core"]
    latency = fat_env["latency"]
    storage = fat_env["storage"]
    acc = core.item_accuracy

    kpi_table = [
        {
            "no": 1, "name": "자동검사율",
            "measured": f"{core.auto_rate_pct:.3f}%",
            "target": "100%",
            "passed": core.auto_rate_pct >= mt.AUTO_RATE_MIN,
        },
        {
            "no": 2, "name": "항목별 판정 정확도(최저)",
            "measured": f"{acc.min_accuracy_pct:.3f}%",
            "target": "≥95%",
            "passed": acc.passed,
        },
        {
            "no": 3, "name": "검사 처리속도 p95",
            "measured": f"{latency.p95_ms:.3f}ms",
            "target": "≤300ms",
            "passed": latency.passed,
        },
        {
            "no": 4, "name": "데이터 저장 & MES 연계율",
            "measured": f"저장 {storage.storage_rate_pct:.3f}% / 연계 {storage.mes_rate_pct:.3f}%",
            "target": "100%",
            "passed": storage.passed,
        },
    ]
    overall = all(r["passed"] for r in kpi_table)

    payload = {
        "title": "AIVIS FAT 결과서 (§1.2 인수 합격기준 자동검증)",
        "dataset_source": "synthetic (gen_synthetic + 사이드카 정답셋, AIVIS_CAMERA=sim)",
        "sample_count": core.sample_count,
        "overall_passed": overall,
        "kpi_table": kpi_table,
        "metric1_auto_inspection": {
            "sample_count": core.sample_count,
            "completed_count": core.completed_count,
            "auto_rate_pct": round(core.auto_rate_pct, 4),
            "threshold_pct": mt.AUTO_RATE_MIN,
            "passed": core.auto_rate_pct >= mt.AUTO_RATE_MIN,
        },
        "metric2_item_accuracy": acc.as_dict(),
        "metric3_latency": latency.as_dict(),
        "metric4_storage_mes": storage.as_dict(),
        "notes": [
            "합성 데이터는 라벨이 명확하여 폴백 CV 로도 항목 정확도가 높게 산출됨.",
            "임계값은 ItemMaster(기준정보)에서 읽음 — 하드코딩 아님.",
            "지표4: POST /inspection(내부토큰) 적재 → DB 조회 + MES watchdog 1회 연계.",
        ],
    }
    paths = write_reports(_REPORT_DIR, "fat_metrics", payload)
    payload["_report_paths"] = paths
    return payload


# ---------------------------------------------------------------------------
# 지표별 차단 테스트 (미달 시 FAIL).
# ---------------------------------------------------------------------------
def test_metric1_auto_inspection_rate_100(fat_env, fat_payload):
    core = fat_env["core"]
    failed = [r.path for r in core.runs if not r.completed]
    assert core.auto_rate_pct >= mt.AUTO_RATE_MIN, (
        f"자동검사율 {core.auto_rate_pct:.3f}% < 100% — 미판정 {len(failed)}건: {failed[:5]}"
    )


def test_metric2_item_accuracy_min_95(fat_env, fat_payload):
    acc = fat_env["core"].item_accuracy
    detail = {k: round(v.accuracy_pct, 3) for k, v in acc.per_item.items()}
    assert acc.passed, (
        f"항목별 정확도 최저 {acc.min_accuracy_pct:.3f}% < 95% — 항목별: {detail}"
    )


def test_metric3_latency_p95_under_300(fat_env, fat_payload):
    lat = fat_env["latency"]
    assert lat.count >= _LATENCY_N, f"배치 표본 {lat.count} < {_LATENCY_N}"
    assert lat.passed, (
        f"처리속도 p95 {lat.p95_ms:.3f}ms > 300ms "
        f"(p50={lat.p50_ms:.1f} p99={lat.p99_ms:.1f} max={lat.max_ms:.1f})"
    )


def test_metric4_storage_and_mes_rate_100(fat_env, fat_payload):
    st = fat_env["storage"]
    assert st.storage_rate_pct >= mt.STORAGE_MES_RATE_MIN, (
        f"저장율 {st.storage_rate_pct:.3f}% < 100% (주입 {st.injected} / 저장 {st.stored})"
    )
    assert st.mes_rate_pct >= mt.STORAGE_MES_RATE_MIN, (
        f"MES 연계율 {st.mes_rate_pct:.3f}% < 100% (연계 {st.mes_synced} / 주입 {st.injected})"
    )


def test_fat_reports_written(fat_payload):
    paths = fat_payload["_report_paths"]
    assert Path(paths["json"]).exists() and Path(paths["md"]).exists()
    assert fat_payload["overall_passed"], "FAT 종합 판정 FAIL — 리포트 참조"
