"""검사 이미지 저장 + 오버레이 렌더 테스트 (M7 일부, §6.4).

검증:
- raw/result 파일이 실제 디스크에 생성된다.
- 파일명 §6.4 규격({LOT}_{Item}_{YYYYMMDDHHmmssSSS}_{verdict}.jpg, ms 3자리).
- 반환 경로는 images_dir 기준 상대경로(절대경로 아님).
- review_flag=True 면 review/ 사본도 생성된다.
- 오버레이는 결정적(동일 입력 2회 동일 바이트/shape).
- 파일명 토큰 안전화(경로 문자 제거).
- 디스크 쓰기 실패 시 graceful(예외 삼키고 error 보고, 경로 None).
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

_SERVICES_DIR = Path(__file__).resolve().parents[2]
if str(_SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_DIR))

from aivis_types import (  # noqa: E402
    DefectCode,
    ItemMaster,
    LengthResult,
    SurfaceResult,
    Verdict,
    VerdictResult,
)

from vision.imaging import (  # noqa: E402
    build_filename,
    render_overlay,
    save_inspection_images,
    save_raw,
    save_result,
)
from vision.imaging.save import _safe_token  # noqa: E402

_FNAME_RE = re.compile(
    r"^[A-Za-z0-9._-]+_[A-Za-z0-9._-]+_\d{17}_(OK|NG)\.jpg$"
)


def _ts() -> datetime:
    return datetime(2026, 6, 9, 14, 12, 33, 456789, tzinfo=timezone.utc)


def _frame() -> np.ndarray:
    rng = np.random.default_rng(7)
    return rng.integers(0, 255, size=(300, 800, 3), dtype=np.uint8)


def _verdict(*, final=Verdict.NG, review=False, codes=None) -> VerdictResult:
    return VerdictResult(
        final_verdict=final,
        defect_codes=codes if codes is not None else [DefectCode.SCR],
        confidence=0.87,
        review_flag=review,
        length=LengthResult(
            ref_length_mm=125.0,
            meas_length_mm=124.7,
            deviation_mm=-0.3,
            length_verdict=Verdict.OK,
            edge_detected=True,
            proc_time_ms=4,
        ),
        surface=SurfaceResult(
            oil_score=0.11,
            discolor_score=0.05,
            scratch_score=0.42,
            surface_verdict=Verdict.NG,
            defect_codes=[DefectCode.SCR],
            proc_time_ms=6,
        ),
        proc_time_ms=42,
    )


def _item() -> ItemMaster:
    return ItemMaster(
        item_code="HP12",
        item_name="Header Pipe 12",
        ref_length_mm=125.0,
        tol_plus_mm=3.0,
        tol_minus_mm=3.0,
        px_to_mm_scale=0.25,
        oil_threshold=0.30,
        discolor_threshold=0.20,
        scratch_threshold=0.15,
    )


# --- 파일명 규격 ---
def test_build_filename_matches_spec():
    fn = build_filename("LOT1", "HP12", _ts(), "OK")
    assert fn == "LOT1_HP12_20260609141233456_OK.jpg"
    assert _FNAME_RE.match(fn)
    # ms 는 정확히 3자리(456789us → 456ms).
    assert fn.split("_")[2] == "20260609141233456"


def test_build_filename_sanitizes_tokens():
    fn = build_filename("LOT/../x 1", "HP\\12:A", _ts(), "weird")
    # 경로/공백 문자 제거, verdict 는 OK 아니면 NG.
    assert "/" not in fn and "\\" not in fn and " " not in fn and ":" not in fn
    assert fn.endswith("_NG.jpg")
    assert _FNAME_RE.match(fn)


def test_safe_token_fallback():
    assert _safe_token("") == "NA"
    assert _safe_token("///") == "NA"
    assert _safe_token("HP-12_A.1") == "HP-12_A.1"


# --- raw 저장 ---
def test_save_raw_creates_file_and_returns_relative(tmp_path):
    rel = save_raw(_frame(), str(tmp_path), "LOT1", "HP12", _ts(), "OK")
    assert rel == "raw/LOT1_HP12_20260609141233456_OK.jpg"
    assert not Path(rel).is_absolute()
    assert (tmp_path / rel).exists()
    # 하위 디렉터리 자동 생성.
    for sub in ("raw", "result", "review"):
        assert (tmp_path / sub).is_dir()


# --- result 저장 + review 라우팅 ---
def test_save_result_no_review(tmp_path):
    overlay = render_overlay(_frame(), _verdict())
    rel = save_result(overlay, str(tmp_path), "LOT1", "HP12", _ts(), "NG")
    assert rel == "result/LOT1_HP12_20260609141233456_NG.jpg"
    assert (tmp_path / rel).exists()
    # review 사본 없음.
    assert not list((tmp_path / "review").glob("*.jpg"))


def test_save_result_with_review_copies_to_review(tmp_path):
    overlay = render_overlay(_frame(), _verdict(review=True))
    rel = save_result(
        overlay, str(tmp_path), "LOT1", "HP12", _ts(), "NG", review_flag=True
    )
    assert rel.startswith("result/")
    review_files = list((tmp_path / "review").glob("*.jpg"))
    assert len(review_files) == 1
    assert review_files[0].name == "LOT1_HP12_20260609141233456_NG.jpg"


# --- 오버레이 결정성 ---
def test_render_overlay_deterministic():
    frame = _frame()
    v = _verdict()
    a = render_overlay(frame, v, item=_item())
    b = render_overlay(frame, v, item=_item())
    assert a.shape == frame.shape
    assert np.array_equal(a, b)
    # 원본은 변형되지 않는다(copy 보장).
    assert not np.array_equal(a, frame)


def test_render_overlay_ok_vs_ng_differ():
    frame = _frame()
    ok = render_overlay(frame, _verdict(final=Verdict.OK, codes=[]))
    ng = render_overlay(frame, _verdict(final=Verdict.NG))
    assert not np.array_equal(ok, ng)


def test_render_overlay_rejects_non_bgr():
    with pytest.raises(ValueError):
        render_overlay(np.zeros((10, 10), dtype=np.uint8), _verdict())


# --- 일괄 진입점 ---
def test_save_inspection_images_full(tmp_path):
    out = save_inspection_images(
        _frame(),
        _verdict(review=True),
        images_dir=str(tmp_path),
        lot="LOT1",
        item_code="HP12",
        inspected_at=_ts(),
        item=_item(),
    )
    assert out.error is None
    assert out.raw_image_path == "raw/LOT1_HP12_20260609141233456_NG.jpg"
    assert out.result_image_path == "result/LOT1_HP12_20260609141233456_NG.jpg"
    assert (tmp_path / out.raw_image_path).exists()
    assert (tmp_path / out.result_image_path).exists()
    # raw/result 파일명 타임스탬프가 동일(짝 보장).
    assert Path(out.raw_image_path).name == Path(out.result_image_path).name
    # review 사본.
    assert list((tmp_path / "review").glob("*.jpg"))


def test_save_inspection_images_uses_env_default(monkeypatch, tmp_path):
    monkeypatch.setenv("AIVIS_IMAGES_DIR", str(tmp_path))
    out = save_inspection_images(
        _frame(),
        _verdict(),
        lot="LOT9",
        item_code="HP12",
        inspected_at=_ts(),
    )
    assert out.error is None
    assert (tmp_path / out.raw_image_path).exists()


# --- 디스크 쓰기 실패 graceful ---
def test_save_inspection_images_graceful_on_write_failure(monkeypatch, tmp_path):
    import vision.imaging.save as save_mod

    def boom(path, image):
        raise OSError("disk full")

    monkeypatch.setattr(save_mod, "_imwrite", boom)
    out = save_inspection_images(
        _frame(),
        _verdict(),
        images_dir=str(tmp_path),
        lot="LOT1",
        item_code="HP12",
        inspected_at=_ts(),
    )
    # 예외를 삼키고 error 를 보고, 경로는 None(검사결과 적재는 막지 않는다).
    assert out.error is not None and "OSError" in out.error
    assert out.raw_image_path is None
    assert out.result_image_path is None
