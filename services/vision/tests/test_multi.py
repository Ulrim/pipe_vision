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


# -------- 다발 동시 절단: 20개를 넘는 설정이 조용히 깎이지 않아야 한다 --------

def test_generator_cap_matches_segmenter_cap():
    """합성 생성기가 분할기보다 먼저 깎으면 다중튜브 테스트가 전부 허수가 된다.

    예전 생성기는 min(20, n) 으로 캡을 걸었다. 그래서 make_multi_image(30) 이
    **20개짜리** 이미지를 주고, 테스트는 그걸 30조각으로 쪼갠 결과를 통과시켰다.
    아무것도 검증하지 않는 테스트였다.
    """
    from vision.multi.segment import MAX_TUBES_HARD

    _img, boxes = make_multi_image(MAX_TUBES_HARD)
    assert len(boxes) == MAX_TUBES_HARD


@pytest.mark.parametrize("n", [32, 64])
def test_segment_above_old_cap(n):
    """20을 넘는 다발을 자동으로도, 기대개수 지정으로도 전부 찾아야 한다."""
    img, boxes = make_multi_image(n)
    assert len(boxes) == n, "생성기가 먼저 깎으면 아래 검증이 무의미하다"
    assert len(segment_tubes(img)) == n                      # 자동
    assert len(segment_tubes(img, expected_count=n)) == n     # 기대 지정


@pytest.mark.parametrize("n", [32, 64])
def test_batch_inspects_every_tube_above_old_cap(n, item):
    """inspect_batch 의 max_tubes 가 20 으로 박혀 있어 44개가 조용히 사라졌다.

    자동 검출이 20 에서 잘리면 기대개수와 불일치해 20개짜리 결과로 떨어진다.
    예외도 경고도 없이 튜브가 사라지므로 수량이 틀린 채로 결과가 쌓인다.
    """
    img, _ = make_multi_image(n)
    res = inspect_batch(img, item, expected_count=n)
    assert len(res.tubes) == n
    assert res.count_detected == n


def test_max_tubes_hard_covers_bundle_cutting():
    """하드 상한이 현장 다발 규모(20 초과)를 담아야 한다."""
    from vision.multi.segment import MAX_TUBES_HARD

    assert MAX_TUBES_HARD >= 32


def test_schema_cap_matches_segmenter_cap():
    """스키마 상한과 분할기 상한이 어긋나면 한쪽에서 조용히 깎인다."""
    from aivis_types.inspection import MAX_EXPECTED_COUNT
    from vision.multi.segment import MAX_TUBES_HARD

    assert MAX_EXPECTED_COUNT == MAX_TUBES_HARD


def test_isolated_bright_row_does_not_stretch_band():
    """밴드에서 **떨어진** 밝은 줄이 밴드를 화면 전체로 늘리면 안 된다.

    예전 _foreground_band 는 임계를 넘는 행의 min~max 를 밴드로 삼았다. 그래서
    튜브 띠와 무관한 밝은 한 줄(설비 반사, 조명 띠 등)만 걸려도 밴드가 프레임
    전체로 벌어지고, 배경까지 튜브 스트립으로 잘렸다.
    """
    img, _ = make_multi_image(5)
    h, w = img.shape[:2]
    tall = np.full((h * 3, w, 3), 20, dtype=np.uint8)
    tall[h * 2 :] = img                      # 튜브 다발은 아래쪽에
    tall[5:9, :] = 255                       # 멀리 떨어진 밝은 줄

    rois = segment_tubes(tall)
    assert len(rois) == 5, f"밝은 줄 때문에 {len(rois)}개로 잘렸다"
    # 모든 스트립이 실제 다발 영역(아래 1/3) 안에 있어야 한다.
    assert min(r.y0 for r in rois) >= h * 2 - 5
