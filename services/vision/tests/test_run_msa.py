"""MSA 제품 코드(vision.quality.msa) + CLI(tools/run_msa.py) 테스트 — §5 M3.

- run_msa 가 결정적 파이프라인에서 반복성 σ≈0, passed=True.
- write_msa_reports 가 msa_<item>.json + .md 를 남긴다.
- CLI(main) 가 --image/--ref-length 오프라인 경로로 리포트 파일을 생성.
- CLI 가 --camera sim 으로도 동작.
- tests/harness.msa re-export 호환(기존 임포트 경로 유지).
합성 이미지 자립, 결정적.
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import pytest
from aivis_types import ItemMaster

_SERVICES_DIR = Path(__file__).resolve().parents[2]
if str(_SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_DIR))

from vision.quality.msa import run_msa, write_msa_reports  # noqa: E402
from vision.tools.gen_synthetic import make_image  # noqa: E402
from vision.tools.run_msa import main  # noqa: E402


def _item() -> ItemMaster:
    return ItemMaster(
        item_code="HP12",
        item_name="Header Pipe 12",
        ref_length_mm=125.0,
        tol_plus_mm=3.0,
        tol_minus_mm=3.0,
        px_to_mm_scale=0.25,
    )


# --- 제품 코드 run_msa ---
def test_run_msa_repeatability_zero_and_pass():
    img, _ = make_image("OK")
    res = run_msa(img, _item(), repeats=8)
    assert res.repeatability_std_mm == pytest.approx(0.0, abs=1e-9)
    assert res.passed is True


def test_run_msa_deterministic():
    img, _ = make_image("OK")
    a = run_msa(img, _item(), repeats=6, appraiser_insets=[0.05, 0.06])
    b = run_msa(img.copy(), _item(), repeats=6, appraiser_insets=[0.05, 0.06])
    assert a.as_dict() == b.as_dict()


def test_write_msa_reports_creates_files(tmp_path):
    img, _ = make_image("OK")
    res = run_msa(img, _item(), repeats=5)
    paths = write_msa_reports(tmp_path, "HP12", res)
    assert Path(paths["json"]).exists()
    assert Path(paths["md"]).exists()
    assert Path(paths["json"]).name == "msa_HP12.json"


# --- CLI ---
def test_cli_offline_image_generates_reports(tmp_path, capsys):
    img, _ = make_image("OK")
    p = tmp_path / "sample.jpg"
    cv2.imwrite(str(p), img)
    out = tmp_path / "msa_out"
    code = main(
        [
            "--item", "HP12",
            "--image", str(p),
            "--repeats", "5",
            "--ref-length", "125.0",
            "--tol-plus", "3.0",
            "--tol-minus", "3.0",
            "--scale", "0.25",
            "--out", str(out),
        ]
    )
    assert code == 0
    assert (out / "msa_HP12.json").exists()
    assert (out / "msa_HP12.md").exists()
    captured = capsys.readouterr().out
    assert "MSA" in captured
    assert "%GR&R" in captured
    assert "합격" in captured or "불합격" in captured


def test_cli_camera_sim_generates_reports(tmp_path):
    out = tmp_path / "msa_sim"
    ds = tmp_path / "ds"
    code = main(
        [
            "--item", "HP12",
            "--camera", "sim",
            "--repeats", "3",
            "--ref-length", "125.0",
            "--scale", "0.25",
            "--dataset-dir", str(ds),
            "--view", "SIDE",
            "--out", str(out),
        ]
    )
    assert code == 0
    assert (out / "msa_HP12.json").exists()


def test_cli_errors_without_item_source(tmp_path):
    img, _ = make_image("OK")
    p = tmp_path / "s.jpg"
    cv2.imwrite(str(p), img)
    # --ref-length 도 --api-url 도 없으면 기준정보 미확보 → 오류 코드 2.
    code = main(["--item", "HP12", "--image", str(p)])
    assert code == 2


def test_cli_errors_without_target(tmp_path):
    # 이미지도 카메라도 없으면 오류 코드 2.
    code = main(["--item", "HP12", "--ref-length", "125.0", "--scale", "0.25"])
    assert code == 2
