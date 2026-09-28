"""촬영본 전수 판독 (부록 A.3).

이 도구의 존재 이유는 "수백 장을 눈으로 넘기면 놓친다" 는 것이다. 따라서
**탐지가 실제로 발동하는지**가 핵심이다. 아무것도 못 잡는 검사기는 안 만드느니만
못하다 — 데이터가 멀쩡하다고 잘못 안심시키기 때문이다.

그래서 결함 유형별로 일부러 망친 사진을 만들어, 각각이 해당 사유로 걸리는지 본다.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from vision.tools.assess_capture import (
    assess_dir,
    assess_image,
    summarize,
    to_markdown,
)
from vision.tools.gen_synthetic import make_image


def _write(path: Path, img: np.ndarray) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img)
    return path


@pytest.fixture()
def ok_img() -> np.ndarray:
    img, _ = make_image("OK")
    return img


def test_clean_image_is_measurable(tmp_path: Path, ok_img: np.ndarray) -> None:
    """정상 촬영본은 ROI·끝단이 모두 잡히고 '불가' 가 아니다."""
    a = assess_image(_write(tmp_path / "ok.jpg", ok_img))
    assert a.roi_found and a.ends_found
    assert a.verdict != "불가", a.reasons
    assert a.pipe_len_px and a.pipe_len_px > 0


def test_blur_is_rejected(tmp_path: Path, ok_img: np.ndarray) -> None:
    """흔들린 사진은 거부한다 — 끝단이 뭉개지면 길이 오차가 그대로 커진다."""
    a = assess_image(_write(tmp_path / "b.jpg", cv2.GaussianBlur(ok_img, (31, 31), 0)))
    assert a.verdict == "불가"
    assert any("흔들림" in r for r in a.reasons)


def test_reflection_saturation_is_rejected(tmp_path: Path, ok_img: np.ndarray) -> None:
    """반사로 날아간 영역은 표면 정보가 이미 소실된 것이라 판정에 쓸 수 없다."""
    sat = ok_img.copy()
    sat[:, 200:600] = 255
    a = assess_image(_write(tmp_path / "s.jpg", sat))
    assert a.verdict == "불가"
    assert any("반사 포화" in r for r in a.reasons)
    assert a.saturated_pct and a.saturated_pct > 5.0


def test_underexposed_is_rejected(tmp_path: Path, ok_img: np.ndarray) -> None:
    a = assess_image(_write(tmp_path / "d.jpg", (ok_img * 0.05).astype(np.uint8)))
    assert a.verdict == "불가"
    assert any("노출 부족" in r for r in a.reasons)


def test_low_resolution_is_rejected(tmp_path: Path, ok_img: np.ndarray) -> None:
    a = assess_image(_write(tmp_path / "t.jpg", cv2.resize(ok_img, (320, 120))))
    assert a.verdict == "불가"
    assert any("해상도 부족" in r for r in a.reasons)


def test_multiple_objects_in_one_frame_is_rejected(tmp_path: Path) -> None:
    """다발 촬영 검출 — 1차 현장 자료가 학습에 못 쓰인 가장 큰 이유(부록 A.0).

    전처리 마스크로 세면 preprocess 가 이미 1개만 골라낸 뒤라 항상 1이 나온다.
    그 회귀를 막기 위한 테스트다.
    """
    multi = np.full((300, 800, 3), 30, np.uint8)
    for y in (60, 140, 220):
        multi[y : y + 40, 100:700] = 185
    a = assess_image(_write(tmp_path / "m.jpg", multi))
    assert a.blob_count == 3, f"덩어리 수 오검출: {a.blob_count}"
    assert a.verdict == "불가"
    assert any("1프레임 1개" in r for r in a.reasons)


def test_edge_touch_is_warned(tmp_path: Path) -> None:
    """가장자리에 닿으면 전장이 안 들어왔을 수 있다 — 막지는 않고 경고."""
    wide, _ = make_image("OK", pipe_len_px=798)
    a = assess_image(_write(tmp_path / "e.jpg", wide))
    assert a.touches_edge
    assert any("가장자리" in r for r in a.reasons)


def test_unreadable_file_is_rejected_not_raised(tmp_path: Path) -> None:
    """깨진 파일 한 장이 전체 판독을 멈추면 안 된다."""
    bad = tmp_path / "x.jpg"
    bad.write_bytes(b"not an image")
    a = assess_image(bad)
    assert a.verdict == "불가"
    assert any("읽을 수 없음" in r for r in a.reasons)


def test_pipeline_error_surfaces_in_reasons(tmp_path: Path, monkeypatch) -> None:
    """판독 중 예외가 조용히 삼켜지면 안 된다.

    예전에 판정 함수가 사유 목록을 통째로 덮어써서 예외 메시지가 사라졌고,
    '끝단 미검출' 로만 보여 원인을 찾는 데 시간을 썼다.
    """
    import vision.tools.assess_capture as mod

    def boom(*_a, **_kw):
        raise RuntimeError("의도적 실패")

    monkeypatch.setattr(mod, "preprocess", boom)
    img, _ = make_image("OK")
    a = assess_image(_write(tmp_path / "p.jpg", img))
    assert a.verdict == "불가"
    assert any("파이프라인 예외" in r and "의도적 실패" in r for r in a.reasons)


def test_dir_scan_and_report(tmp_path: Path, ok_img: np.ndarray) -> None:
    """폴더 전수 판독 → 집계 + 사람이 읽는 보고서."""
    _write(tmp_path / "a" / "ok1.jpg", ok_img)
    _write(tmp_path / "b" / "ok2.jpg", ok_img)
    _write(tmp_path / "b" / "blur.jpg", cv2.GaussianBlur(ok_img, (31, 31), 0))

    items = assess_dir(tmp_path)
    assert len(items) == 3, "하위 폴더까지 모두 훑어야 한다"

    s = summarize(items)
    assert s["total"] == 3
    assert s["verdicts"]["불가"] == 1
    assert s["length_measurable"] >= 2

    md = to_markdown(items, s)
    assert "촬영본 전수 판독 결과" in md
    assert "blur.jpg" in md, "문제 있는 사진은 보고서에 이름이 나와야 한다"


def test_assessment_is_deterministic(tmp_path: Path, ok_img: np.ndarray) -> None:
    """같은 사진은 항상 같은 판정 — 재실행할 때마다 결론이 바뀌면 신뢰할 수 없다."""
    p = _write(tmp_path / "det.jpg", ok_img)
    first, second = assess_image(p), assess_image(p)
    assert first.as_dict() == second.as_dict()
