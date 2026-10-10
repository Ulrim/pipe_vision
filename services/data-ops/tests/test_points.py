"""단면 점 라벨(크레이트 계수 정답셋) 테스트."""
from __future__ import annotations

import json
import os

import pytest
from labeling.points import (
    AUTO_SUFFIX,
    GT_SUFFIX,
    PointLabel,
    PointLabelError,
    PointSet,
    auto_path,
    gt_path,
    load_points,
    match_points,
    save_points,
    score_dir,
)


def _write_img(root: str, name: str) -> str:
    p = os.path.join(root, name)
    with open(p, "wb") as fh:  # 내용은 안 읽는다(채점은 cv2 불필요)
        fh.write(b"\xff\xd8\xff")
    return p


def _ps(pts, source="auto"):
    return PointSet(image="x.jpg", width=100, height=100, source=source,
                    points=[PointLabel(*t) for t in pts])


def test_roundtrip(tmp_path):
    p = str(tmp_path / "a.points.json")
    save_points(p, _ps([(1.0, 2.0, 3.0), (4.0, 5.0, 6.0)], "human"))
    got = load_points(p)
    assert got.source == "human"
    assert [(q.x, q.y, q.r) for q in got.points] == [(1, 2, 3), (4, 5, 6)]


def test_load_rejects_malformed(tmp_path):
    p = str(tmp_path / "bad.points.json")
    with open(p, "w") as fh:
        json.dump({"nope": 1}, fh)
    with pytest.raises(PointLabelError):
        load_points(p)


def test_match_exact():
    gt = [PointLabel(10, 10, 5), PointLabel(50, 50, 5)]
    assert match_points(list(gt), gt) == (2, 0, 0)


def test_match_counts_extra_and_missing():
    gt = [PointLabel(10, 10, 5)]
    pred = [PointLabel(10, 10, 5), PointLabel(80, 80, 5)]
    assert match_points(pred, gt) == (1, 1, 0)
    assert match_points([], gt) == (0, 0, 1)


def test_match_is_one_to_one():
    """예측 2개가 정답 1개에 몰려도 TP 는 1 이다(중복 검출이 공짜면 안 된다)."""
    gt = [PointLabel(10, 10, 5)]
    pred = [PointLabel(10, 10, 5), PointLabel(11, 11, 5)]
    assert match_points(pred, gt) == (1, 1, 0)


def test_fallback_radius_used_when_human_marks_point_only():
    """사람이 점만 찍어 r=0 이면 기준 반경이 0 이라 아무것도 안 맞는다."""
    gt = [PointLabel(10, 10, 0)]
    pred = [PointLabel(12, 10, 4)]
    assert match_points(pred, gt) == (0, 1, 1)
    assert match_points(pred, gt, fallback_radius=5.0) == (1, 0, 0)


def test_score_dir_skips_unlabelled_images(tmp_path):
    """정답이 없는 이미지는 '정답 0개'가 아니라 **아직 라벨 안 함**이다.

    집계에 넣으면 그 이미지의 제안이 전부 오검으로 잡혀 정밀도가 무너진다.
    """
    root = str(tmp_path)
    a = _write_img(root, "a.jpg")
    b = _write_img(root, "b.jpg")
    save_points(auto_path(a), _ps([(10, 10, 5)]))
    save_points(gt_path(a), _ps([(10, 10, 5)], "human"))
    save_points(auto_path(b), _ps([(10, 10, 5), (40, 40, 5)]))  # 정답 없음

    r = score_dir(root)
    assert r["labelled_images"] == 1
    assert r["unlabelled_skipped"] == 1
    assert r["precision"] == 1.0 and r["recall"] == 1.0


def test_score_dir_reports_miss_and_false(tmp_path):
    root = str(tmp_path)
    a = _write_img(root, "a.jpg")
    save_points(auto_path(a), _ps([(10, 10, 5), (80, 80, 5)]))
    save_points(gt_path(a), _ps([(10, 10, 5), (40, 40, 5)], "human"))
    r = score_dir(root)
    assert r["gt_total"] == 2 and r["pred_total"] == 2
    assert r["rows"][0]["tp"] == 1
    assert r["rows"][0]["fp"] == 1
    assert r["rows"][0]["fn"] == 1


def test_suffixes_do_not_collide():
    """auto 와 정답 파일 경로가 겹치면 사람 작업이 덮인다."""
    assert auto_path("/x/a.jpg").endswith(AUTO_SUFFIX)
    assert gt_path("/x/a.jpg").endswith(GT_SUFFIX)
    assert auto_path("/x/a.jpg") != gt_path("/x/a.jpg")


def test_matching_agrees_with_vision_benchmark():
    """vision 쪽 매칭과 결과가 같아야 한다 — 구현이 둘이라 조용히 갈라지면 안 된다."""
    import sys
    from pathlib import Path

    # 저장소 기준 상대경로(종전엔 개발 PC 절대경로라 CI 에서 'No module named vision').
    services = str(Path(__file__).resolve().parents[2])
    if services not in sys.path:
        sys.path.insert(0, services)
    from vision.tools.bundle_benchmark import match as vmatch

    gt = [(10.0, 10.0, 5.0), (50.0, 50.0, 5.0), (90.0, 90.0, 5.0)]
    pred = [(10.0, 12.0), (51.0, 50.0), (200.0, 200.0)]
    mine = match_points(
        [PointLabel(x, y, 0) for x, y in pred],
        [PointLabel(*g) for g in gt],
        fallback_radius=0.0,
    )
    assert mine == vmatch(pred, gt, 1.0)


def test_score_warns_when_gt_is_untouched_copy(tmp_path):
    """교정 안 한 정답(제안 복사본)으로 채점하면 1.0 이 나온다 — 경고해야 한다."""
    root = str(tmp_path)
    a = _write_img(root, "a.jpg")
    pts = _ps([(10, 10, 5), (40, 40, 5)])
    save_points(auto_path(a), pts)
    save_points(gt_path(a), _ps([(10, 10, 5), (40, 40, 5)], "human"))
    r = score_dir(root)
    assert r["f1"] == 1.0
    assert r["gt_untouched"] == 1
    assert r["warning"] and "교정" in r["warning"]


def test_no_warning_once_corrected(tmp_path):
    root = str(tmp_path)
    a = _write_img(root, "a.jpg")
    save_points(auto_path(a), _ps([(10, 10, 5), (40, 40, 5)]))
    save_points(gt_path(a), _ps([(10, 10, 5)], "human"))  # 사람이 하나 지움
    r = score_dir(root)
    assert r["gt_untouched"] == 0
    assert r["warning"] is None
    assert r["rows"][0]["fp"] == 1
