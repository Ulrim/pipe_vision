"""MSA — 길이 반복성/재현성(GR&R 근사), §5 M3 DoD.

동일 샘플 30회 반복 측정 → 반복성/재현성/GR&R 산출. 파이프라인은 결정적이라
반복성이 0 에 수렴해야 한다(측정시스템 우수). 결과를 tests/fat/report 로 남긴다.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.harness import metrics as mt
from tests.harness import msa as msa_mod
from tests.harness.dataset import write_groundtruth_dataset
from tests.harness.report import write_reports

import cv2

_REPORT_DIR = Path(__file__).resolve().parent / "report"
_REPEATS = 30


@pytest.fixture(scope="module")
def msa_result(tmp_path_factory):
    item = mt.make_item_master()
    # 단일 OK 샘플 1장 생성(동일 샘플 반복 측정 대상).
    d = tmp_path_factory.mktemp("msa_ds")
    specs = write_groundtruth_dataset(d, class_counts={"OK": 1}, item_code=item.item_code)
    img = cv2.imread(str(specs[0].path))
    assert img is not None

    # 재현성: surface_inset_ratio 를 측정자/조건 변동으로 모사(3 조건).
    res = msa_mod.run_msa(
        img, item, repeats=_REPEATS, appraiser_insets=[0.05, 0.06, 0.07]
    )
    payload = {
        "title": "AIVIS MSA 분석 결과서 (길이 반복성/재현성, §5 M3)",
        "dataset_source": "synthetic OK 샘플 1장 반복 측정",
        "sample_count": 1,
        "overall_passed": res.passed,
        "kpi_table": [
            {
                "no": "MSA", "name": "%GR&R(공차 대비)",
                "measured": f"{res.pct_grr_tolerance:.4f}%",
                "target": "≤30%",
                "passed": res.passed,
            }
        ],
        "msa": res.as_dict(),
        "notes": [
            f"동일 샘플 {_REPEATS}회 × {res.appraisers}조건 반복 측정.",
            "파이프라인 결정성으로 반복성(EV)이 0 에 수렴 → 측정시스템 우수.",
            "재현성(AV)은 surface_inset_ratio 조건 변동에 대한 길이측정 안정성.",
        ],
    }
    paths = write_reports(_REPORT_DIR, "msa_length", payload)
    return res, paths


def test_msa_repeatability_deterministic(msa_result):
    res, _ = msa_result
    # 결정적 파이프라인: 동일 입력 반복 측정 변동 0(반복성 우수).
    assert res.repeatability_std_mm == pytest.approx(0.0, abs=1e-9), (
        f"반복성 σ={res.repeatability_std_mm} (>0) — 결정성 위반 의심"
    )


def test_msa_grr_within_tolerance(msa_result):
    res, _ = msa_result
    assert res.passed, (
        f"%GR&R {res.pct_grr_tolerance:.4f}% > 30% — 측정시스템 부적합 "
        f"(EV={res.repeatability_std_mm}, AV={res.reproducibility_std_mm})"
    )


def test_msa_report_written(msa_result):
    _, paths = msa_result
    assert Path(paths["json"]).exists() and Path(paths["md"]).exists()
