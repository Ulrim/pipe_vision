"""디버그 시각화 CLI 테스트 (`tools/debug_length.py`) — 진단 도구 자체 검증.

합성 이미지(gen_synthetic)만으로 완전히 자립적으로 통과한다(API/DB/실카메라
불필요). 검증 범위:
- 정상 케이스: 에지 검출 성공, 시각화 파일 생성, 진단 텍스트에 mm 값 포함.
- 실패 케이스: min_contrast 미달 → 경고 문구 포함, edge_detected=False.
- 다중모드(--multi): 개요 + 스트립별 출력 파일 생성.
- 경고 로직 단위 검증(build_diagnostics): 데모 시드값/캘리브레이션 감지.
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

_SERVICES_DIR = Path(__file__).resolve().parents[2]
if str(_SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_DIR))

from aivis_types import ItemMaster  # noqa: E402

from vision.tools.debug_length import (  # noqa: E402
    DEMO_REF_MM,
    DEMO_SCALE,
    build_diagnostics,
    compute_edge_debug,
    main,
)
from vision.tools.gen_synthetic import make_image, make_multi_image  # noqa: E402


@pytest.fixture
def ok_image_path(tmp_path: Path) -> Path:
    img, _bbox = make_image("OK")
    p = tmp_path / "sample_ok.jpg"
    cv2.imwrite(str(p), img)
    return p


# ---------------- 정상 케이스 ----------------


def test_single_ok_case_creates_overlay_and_reports_mm(ok_image_path, capsys):
    code = main([str(ok_image_path)])
    assert code == 0

    out_path = ok_image_path.with_name("sample_ok_debug.jpg")
    assert out_path.exists()
    saved = cv2.imread(str(out_path))
    assert saved is not None
    # 원본보다 하단 프로파일 패널만큼 더 높다(세로로 vstack).
    orig = cv2.imread(str(ok_image_path))
    assert saved.shape[0] > orig.shape[0]
    assert saved.shape[1] == orig.shape[1]

    captured = capsys.readouterr().out
    assert "mm" in captured
    assert "meas_length_mm" in captured
    assert "edge_detected: True" in captured
    assert "length_verdict: OK" in captured


def test_single_ok_case_custom_scale_avoids_demo_warning(ok_image_path, capsys):
    code = main(
        [
            str(ok_image_path),
            "--scale",
            "0.183",
            "--ref-length-mm",
            "91.5",
        ]
    )
    assert code == 0
    captured = capsys.readouterr().out
    assert "데모 시드값" not in captured


def test_default_scale_triggers_demo_seed_warning(ok_image_path, capsys):
    # CLI 기본값(--scale/--ref-length-mm 미지정)은 데모 시드값과 동일 →
    # 웹 캘리브레이션 미실시 의심 경고가 항상 뜬다(rank1 원인 자가진단).
    code = main([str(ok_image_path)])
    assert code == 0
    captured = capsys.readouterr().out
    assert "데모 시드값" in captured
    assert "calibrate" in captured


# ---------------- 실패 케이스(대비 부족) ----------------


def test_min_contrast_gate_failure_reports_warning(ok_image_path, capsys):
    # 실제 대비(~75)보다 훨씬 높은 임계를 강제해 게이트 실패를 결정적으로 재현.
    code = main([str(ok_image_path), "--min-contrast", "500"])
    assert code == 0
    captured = capsys.readouterr().out
    assert "edge_detected: False" in captured
    assert "length_verdict: NG" in captured
    assert "대비" in captured and "임계" in captured
    assert "[FAIL]" in captured


def test_compute_edge_debug_contrast_fields_match_gate(ok_image_path):
    img = cv2.imread(str(ok_image_path))
    item = ItemMaster(
        item_code="HP12",
        item_name="Header Pipe",
        ref_length_mm=125.0,
        tol_plus_mm=0.5,
        tol_minus_mm=0.5,
        px_to_mm_scale=0.25,
    )
    dbg = compute_edge_debug(img, item, min_contrast=500.0)
    assert dbg.contrast < dbg.min_contrast
    assert dbg.length.edge_detected is False
    warnings, _notes = build_diagnostics(dbg, item, capture_recipe=None)
    assert any("대비" in w and "임계" in w for w in warnings)


# ---------------- 다중모드(--multi) ----------------


def test_multi_mode_creates_overview_and_per_tube_outputs(tmp_path, capsys):
    img, _boxes = make_multi_image(3)
    p = tmp_path / "multi3.jpg"
    cv2.imwrite(str(p), img)

    code = main([str(p), "--multi", "3"])
    assert code == 0

    overview = p.with_name("multi3_debug.jpg")
    assert overview.exists()
    for i in (1, 2, 3):
        tube_out = p.with_name(f"multi3_tube{i}_debug.jpg")
        assert tube_out.exists()
        assert cv2.imread(str(tube_out)) is not None

    captured = capsys.readouterr().out
    assert "다중튜브 디버그 진단" in captured
    assert "세그멘테이션 결과: 3개" in captured
    assert "Tube #1" in captured and "Tube #2" in captured and "Tube #3" in captured


def test_multi_mode_mismatch_warning_when_wrong_count(tmp_path, capsys):
    img, _boxes = make_multi_image(3)
    p = tmp_path / "multi3_wrong.jpg"
    cv2.imwrite(str(p), img)

    # 실제로는 3개인데 5개라고 잘못 지정 → 자동검출과 불일치 경고(rank8 유형).
    code = main([str(p), "--multi", "5"])
    assert code == 0
    captured = capsys.readouterr().out
    assert "MISMATCH" in captured or "다릅니다" in captured


# ---------------- 오류 처리 ----------------


def test_missing_file_returns_error_code(tmp_path):
    missing = tmp_path / "nope.jpg"
    code = main([str(missing)])
    assert code == 2


# ---------------- 경고 로직 단위 테스트 ----------------


def test_build_diagnostics_flags_missing_capture_recipe_keys(ok_image_path):
    img = cv2.imread(str(ok_image_path))
    item = ItemMaster(
        item_code="HP12",
        item_name="Header Pipe",
        ref_length_mm=DEMO_REF_MM,
        tol_plus_mm=0.5,
        tol_minus_mm=0.5,
        px_to_mm_scale=DEMO_SCALE,
    )
    dbg = compute_edge_debug(img, item, min_contrast=20.0)
    warnings, _notes = build_diagnostics(
        dbg, item, capture_recipe={"lighting": "raking"}
    )
    assert any("af_mode" in w for w in warnings)


def test_build_diagnostics_no_note_when_capture_recipe_complete(ok_image_path):
    img = cv2.imread(str(ok_image_path))
    item = ItemMaster(
        item_code="HP12",
        item_name="Header Pipe",
        ref_length_mm=DEMO_REF_MM,
        tol_plus_mm=0.5,
        tol_minus_mm=0.5,
        px_to_mm_scale=DEMO_SCALE,
    )
    dbg = compute_edge_debug(img, item, min_contrast=20.0)
    warnings, notes = build_diagnostics(
        dbg, item, capture_recipe={"af_mode": "manual", "exposure_us": 8000}
    )
    assert not any("af_mode" in w for w in warnings)
    assert not any("capture-recipe 미입력" in n for n in notes)


def test_flat_frame_reports_polarity_and_border_warning():
    # 완전 평탄(대비 0) 프레임 — cv2 Otsu 는 threshold=0 을 골라 전체 프레임을
    # 전경으로 오인한다(segment_pipe_roi 에 flat-frame 가드가 없음, 실제 동작).
    # length_roi=전체 프레임(모든 변에 닿음) → 폴라리티/경계 의심 경고를 낸다.
    blank = np.full((300, 800, 3), 30, dtype=np.uint8)
    item = ItemMaster(
        item_code="HP12",
        item_name="Header Pipe",
        ref_length_mm=125.0,
        tol_plus_mm=0.5,
        tol_minus_mm=0.5,
        px_to_mm_scale=0.25,
    )
    dbg = compute_edge_debug(blank, item, min_contrast=20.0)
    assert dbg.length_roi is not None
    assert dbg.roi_touches_border is True
    assert dbg.mask_border_fg_ratio == pytest.approx(1.0)
    warnings, _notes = build_diagnostics(dbg, item, capture_recipe=None)
    assert any("폴라리티" in w for w in warnings)
    assert any("프레임 경계" in w for w in warnings)
