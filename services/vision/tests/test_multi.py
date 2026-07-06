"""다객체(다중 튜브) 검사 테스트 — 분할/개수/결함국소화/불일치/graceful/결정성.

모든 입력은 합성(gen_synthetic.make_multi_image)이며 AIVIS_CAMERA 무관하게
결정적이다. 기존 단일 튜브 모듈(measure_length/analyze_surface)을 재사용한다.
"""
from __future__ import annotations

import numpy as np
import pytest
from aivis_types import Verdict

from vision.imaging import render_batch_overlay
from vision.imaging.save import _GREEN, _RED
from vision.multi import inspect_batch, segment_tubes
from vision.tools.gen_synthetic import make_multi_image


# ---------------- 분할: 붙어있는 N개 정확 분리 ----------------

@pytest.mark.parametrize("n", [5, 13, 20])
def test_segment_touching_count(n):
    img, _ = make_multi_image(n)
    rois = segment_tubes(img)
    assert len(rois) == n
    # index 는 축 순서 1..N.
    assert [r.index for r in rois] == list(range(1, n + 1))
    # 스트립이 세로로 겹치지 않고 순차(축 순서) 정렬.
    ys = [r.y0 for r in rois]
    assert ys == sorted(ys)
    for r in rois:
        assert r.x1 > r.x0 and r.y1 > r.y0
        assert 0.0 <= r.confidence <= 1.0


def test_segment_single_tube_graceful():
    img, _ = make_multi_image(1)
    rois = segment_tubes(img)
    assert len(rois) == 1
    assert rois[0].index == 1


def test_segment_empty_frame_graceful(item):
    blank = np.full((300, 800, 3), 30, dtype=np.uint8)
    rois = segment_tubes(blank)
    assert rois == []
    # 배치도 미판정 없이 graceful(빈 결과, NG).
    b = inspect_batch(blank, item)
    assert b.count_detected == 0
    assert b.batch_verdict == Verdict.NG.value


# ---------------- expected_count 보정 ----------------

def test_expected_count_forces_exact_split():
    img, _ = make_multi_image(13)
    # expected 지정 시 정확히 그 수의 스트립으로 분할(경계 보정).
    rois = segment_tubes(img, expected_count=13)
    assert len(rois) == 13
    ys = [r.y0 for r in rois]
    assert ys == sorted(ys)


def test_expected_count_clamped_to_max():
    img, _ = make_multi_image(5)
    # 상한(max_tubes) 초과 요청은 클램프된다.
    rois = segment_tubes(img, expected_count=99, max_tubes=20)
    assert len(rois) <= 20


# ---------------- 결함 국소화: 특정 index 에서만 검출 ----------------

def test_defect_localized_to_injected_index(item):
    # 0-based: 튜브#3(idx2) 스크래치, 튜브#5(idx4) 길이+.
    img, _ = make_multi_image(6, defects={2: "SCR", 4: "LEN_PLUS"})
    br = inspect_batch(img, item, expected_count=6)
    assert br.count_detected == 6
    by_index = {t.index: t for t in br.tubes}

    scr_tube = by_index[3]
    assert scr_tube.scratch_score > item.scratch_threshold
    assert "SCR" in scr_tube.defect_codes
    assert scr_tube.final_verdict == Verdict.NG.value

    len_tube = by_index[5]
    assert len_tube.length_verdict == Verdict.NG.value
    assert "LEN" in len_tube.defect_codes
    assert len_tube.deviation_mm > item.tol_plus_mm

    # 결함 미주입 튜브(#1,#2,#4,#6)는 SCR/LEN 없음 + OK.
    for idx in (1, 2, 4, 6):
        t = by_index[idx]
        assert "SCR" not in t.defect_codes
        assert "LEN" not in t.defect_codes
        assert t.length_verdict == Verdict.OK.value
        assert t.final_verdict == Verdict.OK.value


def test_all_ok_batch_is_ok(item):
    img, _ = make_multi_image(8)
    br = inspect_batch(img, item, expected_count=8)
    assert br.ng_count == 0
    assert br.count_ok
    assert br.batch_verdict == Verdict.OK.value


# ---------------- 개수 불일치 플래그 ----------------

def test_count_mismatch_flag(item):
    img, _ = make_multi_image(5)  # 실제 5개
    br = inspect_batch(img, item, expected_count=7)  # 7개 기대
    assert br.count_detected == 5
    assert br.count_expected == 7
    assert br.count_mismatch is True
    assert br.count_ok is False
    # 개수 불일치는 전량 OK 라도 배치 NG.
    assert br.batch_verdict == Verdict.NG.value


def test_count_match_no_mismatch(item):
    img, _ = make_multi_image(6)
    br = inspect_batch(img, item, expected_count=6)
    assert br.count_mismatch is False
    assert br.count_ok is True


# ---------------- 결정성 ----------------

def test_segment_deterministic():
    img, _ = make_multi_image(13, defects={4: "SCR"})
    a = segment_tubes(img)
    b = segment_tubes(img)
    assert [r.bbox for r in a] == [r.bbox for r in b]
    assert [r.confidence for r in a] == [r.confidence for r in b]


def test_inspect_batch_deterministic(item):
    img, _ = make_multi_image(8, defects={3: "DIS", 6: "OIL"})
    a = inspect_batch(img, item, expected_count=8)
    b = inspect_batch(img, item, expected_count=8)

    def key(br):
        return [
            (
                t.index,
                t.bbox,
                t.length_mm,
                t.deviation_mm,
                t.length_verdict,
                t.oil_score,
                t.discolor_score,
                t.scratch_score,
                t.final_verdict,
                tuple(t.defect_codes),
            )
            for t in br.tubes
        ]

    assert key(a) == key(b)
    assert a.batch_verdict == b.batch_verdict


# ---------------- 세로 축(axis=vertical) ----------------

def test_vertical_axis(item):
    img, _ = make_multi_image(5, axis="vertical")
    rois = segment_tubes(img, axis="vertical")
    assert len(rois) == 5
    br = inspect_batch(img, item, axis="vertical", expected_count=5)
    assert br.count_detected == 5
    assert br.batch_verdict == Verdict.OK.value


# ---------------- proc_time 계측 ----------------

def test_proc_time_measured(item):
    img, _ = make_multi_image(10)
    br = inspect_batch(img, item, expected_count=10)
    assert br.proc_time_ms >= 0
    assert br.per_tube_avg_ms >= 0.0
    # 튜브당 평균은 단일 검사 예산(300ms/ea) 이내여야 한다.
    assert br.per_tube_avg_ms <= 300.0


# ---------------- 오버레이 ----------------

def test_batch_overlay_boxes_and_colors(item):
    img, _ = make_multi_image(6, defects={2: "SCR", 4: "LEN_PLUS"})
    br = inspect_batch(img, item, expected_count=6)
    ov = render_batch_overlay(img, br)
    assert ov.shape == img.shape
    # 원본과 달라야(박스/라벨이 그려짐).
    assert not np.array_equal(ov, img)

    by_index = {t.index: t for t in br.tubes}
    red_exact = np.all(ov == np.array(_RED), axis=2)
    green_exact = np.all(ov == np.array(_GREEN), axis=2)

    # NG 튜브(#3,#5) bbox 영역에 빨강 테두리 존재.
    for idx in (3, 5):
        x0, y0, x1, y1 = by_index[idx].bbox
        assert red_exact[y0:y1, x0:x1].any()
    # OK 튜브(#1) bbox 영역에 초록 테두리 존재.
    x0, y0, x1, y1 = by_index[1].bbox
    assert green_exact[y0:y1, x0:x1].any()


def test_batch_overlay_deterministic(item):
    img, _ = make_multi_image(7, defects={1: "OIL"})
    br = inspect_batch(img, item, expected_count=7)
    a = render_batch_overlay(img, br)
    b = render_batch_overlay(img, br)
    assert np.array_equal(a, b)
