"""직접 만든 데이터(vision.synth)와 배치 찾기(layout.py) — 2026-10-24.

- 생성기: 결정성, 정답이 화면 안, 표면 학습 샘플의 라벨 규칙.
- 배치 찾기: 어두운 바닥(현장 권장)에서 붙음·틈·섞임, 기울기 ±3°, 기준자 — 개수 정확, 기준자는 빼고 센다.
- 종전 버그 회귀: 다발이 화면 폭의 30% 보다 좁으면 하나도 못 찾았다(60cm·250mm 에서 딱 그 폭).
- 배경 판단: 어두운 바닥의 큰 다발 / 조명이 기운 밝은 바닥.

**직접 만든 데이터 — 인수 증빙 아님.** 여기서는 알고리즘이 정답을 아는 장면에서 맞는지만 본다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

_SERVICES_DIR = Path(__file__).resolve().parents[2]
if str(_SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_DIR))

from vision.multi.batch import inspect_batch  # noqa: E402
from vision.multi.layout import find_layout  # noqa: E402
from vision.preprocess.foreground import light_background  # noqa: E402
from vision.synth.dataset import SURFACE_CLASSES, surface_sample, write_scene_set  # noqa: E402
from vision.synth.scene import SceneSpec, make_scene, random_spec  # noqa: E402
from vision.tools.eval_scenes import _item, evaluate  # noqa: E402

SIZE = (1200, 820)


def _scene(seed: int, **kw):
    spec = SceneSpec(width=SIZE[0], height=SIZE[1], seed=seed, **kw)
    return make_scene(spec)


# --- 생성기 -------------------------------------------------------------------
def test_scene_is_deterministic_and_truth_inside_frame():
    for kind in ("production", "tray", "floor", "hard"):
        spec = random_spec(np.random.default_rng(5), kind=kind, seed=11, size=SIZE)
        a, ga = make_scene(spec)
        b, gb = make_scene(spec)
        assert np.array_equal(a, b)
        assert json.dumps(ga, default=float) == json.dumps(gb, default=float)
        for t in ga["tubes"]:
            q = np.array(t["quad"])
            assert (q[:, 0] >= -1).all() and (q[:, 0] <= SIZE[0]).all(), kind
            assert (q[:, 1] >= -1).all() and (q[:, 1] <= SIZE[1]).all(), kind


def test_overhead_scenes_have_no_visible_bores():
    """위에서 수직으로 찍으면 끝면은 카메라 반대쪽을 본다 — 구멍을 그리면 길이 정답과 어긋난 장면이 된다."""
    assert random_spec(np.random.default_rng(1), kind="production", seed=1).end_faces is False
    assert random_spec(np.random.default_rng(1), kind="hard", seed=1).end_faces is True


def test_surface_sample_labels():
    rng = np.random.default_rng(3)
    for cls in SURFACE_CLASSES:
        img, labels, border, _src = surface_sample(cls, rng)
        assert img.ndim == 3 and img.shape[0] >= 5 and img.shape[1] > img.shape[0]
        if cls == "OK":
            assert labels == [] and not border
        elif cls in ("SCR", "OIL", "DIS"):
            assert labels == [cls] and not border
        elif cls == "MULTI":
            assert "MULTI" in labels and len([x for x in labels if x != "MULTI"]) >= 2
        elif cls == "BORDER":
            assert border and len(labels) == 1


# --- 배치 찾기(어두운 바닥) -------------------------------------------------------
@pytest.mark.parametrize("arrangement,seed", [("touching", 1), ("gap", 2), ("mixed", 3), ("touching", 4)])
def test_layout_counts_dark_background(arrangement, seed):
    img, gt = _scene(seed, background="dark", n_tubes=14, arrangement=arrangement, angle_deg=2.5 if seed % 2 else -2.0,
                     length_mm=300.0, diameter_mm=16.0, ruler=True, jpeg_quality=85)
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    assert light_background(g) is False
    lay = find_layout(g, light=False)
    assert lay.count == gt["n_tubes"], lay.notes
    assert lay.rulers == 1, "기준자(눈금 띠)는 개수에서 뺀다"
    assert not lay.uncertain


def test_narrow_bundle_regression_and_lengths():
    """종전: 다발이 화면 폭의 30% 보다 좁으면(60cm·250mm) 밴드를 못 찾아 0개. 이제 개수·길이 모두."""
    img, gt = _scene(7, background="dark", n_tubes=12, arrangement="gap", angle_deg=-2.8, length_mm=250.0,
                     diameter_mm=12.0, mm_per_px=0.354, ruler=True)
    xs = np.array([p[0] for t in gt["tubes"] for p in t["quad"]])
    assert (xs.max() - xs.min()) / SIZE[0] < 0.75
    b = inspect_batch(img, _item(gt), axis="auto", expected_count=gt["n_tubes"], stage="CUT_LENGTH")
    assert b.count_detected == gt["n_tubes"] and not b.count_mismatch and not b.count_uncertain
    meas = sorted(t.length_mm for t in b.tubes if t.length_mm is not None)
    truth = sorted(t["length_mm"] for t in gt["tubes"])
    assert len(meas) >= gt["n_tubes"] - 1
    # 끝 테두리(절단면 그늘) 때문에 약 1px 짧게 잰다 — 일정한 치우침이라 현장에서는 마스터 시료로 보정한다.
    err = np.median(np.array(meas[:len(truth)]) - np.array(truth[:len(meas)]))
    assert abs(err) < 0.8


def test_light_background_rule():
    big, _ = _scene(9, background="dark", n_tubes=25, arrangement="touching", length_mm=420.0, diameter_mm=22.0)
    assert light_background(cv2.cvtColor(big, cv2.COLOR_BGR2GRAY)) is False, "어두운 바닥의 큰 다발"
    floor, _ = _scene(10, background="floor", n_tubes=5, arrangement="separated", light_gradient=0.3)
    assert light_background(cv2.cvtColor(floor, cv2.COLOR_BGR2GRAY)) is True, "조명이 기운 밝은 바닥"


def test_eval_scenes_runs(tmp_path):
    write_scene_set(tmp_path, {"production": 2}, 20261024, size=SIZE)
    res = evaluate(tmp_path / "scenes", mismatch_every=2)
    s = res["overall"]
    assert s["scenes"] == 2 and s["count_accuracy"] == 1.0
    assert s["mismatch_scenes"] == 1 and s["mismatch_detected"] == 1
    assert res["note"].startswith("직접 만든 데이터")


def test_length_offset_master_calibration():
    """기준정보 촬영 레시피 length_offset_mm(마스터 시료 보정)를 길이에 더하고 편차·판정을 다시 낸다."""
    from aivis_types import ItemMaster

    from vision.pipeline import InspectionPipeline

    img = np.full((200, 800, 3), 25, np.uint8)
    img[80:120, 100:700] = 170                      # 600px 튜브(끝 그늘 없음 — 측정은 정확)
    base = dict(item_code="T", item_name="t", ref_length_mm=600.0, tol_plus_mm=0.3, tol_minus_mm=0.3,
                px_to_mm_scale=1.0, oil_threshold=0.3, discolor_threshold=0.2, scratch_threshold=0.15)
    pipe = InspectionPipeline()
    plain = pipe.run(img, ItemMaster(**base), stage="CUT_LENGTH").length
    shifted = pipe.run(img, ItemMaster(**base, capture_recipe={"length_offset_mm": 0.5}), stage="CUT_LENGTH").length
    assert plain.meas_length_mm == pytest.approx(600.0, abs=0.1)
    assert shifted.meas_length_mm == pytest.approx(plain.meas_length_mm + 0.5, abs=1e-6)
    assert shifted.deviation_mm == pytest.approx(shifted.meas_length_mm - 600.0, abs=1e-6)
    assert str(getattr(plain.length_verdict, "value", plain.length_verdict)) == "OK"
    assert str(getattr(shifted.length_verdict, "value", shifted.length_verdict)) == "NG"


def test_layout_gray_floor_and_dust():
    """덜 검은 바닥(밝기 75)·먼지 점: 전역 Otsu 는 튜브를 가는 띠 둘로 갈라 두 배로 셌다(14 → 28)."""
    img, gt = _scene(21, background="dark", n_tubes=14, arrangement="mixed", angle_deg=1.5, length_mm=300.0,
                     diameter_mm=18.0, bg_level=75.0, dust=40, ruler=True)
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    unit = gt["spec"]["diameter_mm"] / gt["mm_per_px"]          # 기준정보 외경(현장 권장)
    lay = find_layout(g, light=False, unit_px=unit)
    assert lay.count == gt["n_tubes"], lay.notes
    assert not lay.uncertain
    # 외경이 없으면 튜브마다 밝기가 달라 한 개 폭 추정이 틀릴 수 있다 — 그때는 조용히 넘기지 않고 재확인
    blind = find_layout(g, light=False)
    assert blind.count == gt["n_tubes"] or blind.uncertain, blind.notes


def test_field_kind_spec():
    sp = random_spec(np.random.default_rng(3), kind="field", seed=1)
    assert sp.background == "dark" and sp.bg_level is not None and 5 <= sp.n_tubes <= 40
