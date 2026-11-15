"""튜브 중심선 확인(ridge_check.py) — 중심선 잇기, 빼기·더하기 판단, 배치 연동, 모델 로드 (2026-11-15).

모델 자체(고객 사진으로 학습)는 저장소에 없다. 여기서는 지도에서 중심선을 튜브마다 하나씩 뽑는지(끝단에서
빗처럼 이어지지 않는지), 맨 바깥 칸만 빼는지, 몸통이 뚜렷한 칸 밖 중심선만 더하는지, 배치 결과가 그대로
따라가는지, 모델이 없거나 어두운 배경이면 아무것도 바꾸지 않는지만 본다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_SERVICES_DIR = Path(__file__).resolve().parents[2]
if str(_SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_DIR))

from vision.multi import batch as batch_mod  # noqa: E402
from vision.multi import ridge_check  # noqa: E402
from vision.multi.ridge_check import RidgeCheck, RidgeChecker, decide, line_pieces, quad_seg  # noqa: E402

from .test_light_background import _draw_tube, _item, _rows, _scene  # noqa: E402

H, W = 240, 320


def _maps(xs, y0=40, y1=200, sigma=1.0, body_w=3.0):
    """세로 중심선(가우시안 단면)들과 몸통 지도 — 모델 출력 해상도."""
    xx = np.arange(W, dtype=np.float32)[None, :]
    ridge = np.zeros((H, W), np.float32)
    body = np.zeros((H, W), np.float32)
    rows = slice(y0, y1)
    for x in xs:
        ridge[rows] = np.maximum(ridge[rows], np.exp(-((xx - x) ** 2) / (2 * sigma ** 2)))
        body[rows] = np.maximum(body[rows], (np.abs(xx - x) <= body_w).astype(np.float32))
    return ridge, body


def _segs(xs, y0=40, y1=200, w=7.0):
    return [(np.array([x, y0], float), np.array([x, y1 - 1], float), w) for x in xs]


BUNDLE = [100 + 8 * k for k in range(6)]


def test_parallel_lines_become_one_piece_each():
    """8px 간격 중심선 6개 → 조각 6개(끝단에서 서로 이어지지 않는다)."""
    ridge, _ = _maps(BUNDLE)
    pcs = [p for p in line_pieces(ridge, 0.4) if p["L"] > 100]
    assert len(pcs) == 6
    assert sorted(round(float(p["c"][0])) for p in pcs) == BUNDLE


def test_quad_seg_endpoints_any_corner_order():
    q = np.array([[10, 0], [110, 0], [110, 8], [10, 8]], float)
    for qq in (q, q[[1, 2, 3, 0]], q[::-1]):
        a, b, w = quad_seg(qq)
        assert w == pytest.approx(8.0)
        assert sorted([a[0], b[0]]) == pytest.approx([10.0, 110.0])


def test_outer_cell_without_line_is_removed():
    """셈은 7칸(맨 오른쪽은 트레이 벽), 지도에는 6줄 → 맨 바깥 칸 하나만 뺀다."""
    ridge, body = _maps(BUNDLE)
    res = decide(ridge, body, _segs(BUNDLE + [BUNDLE[-1] + 8]))
    assert res.removed == [6] and not res.added


def test_inner_cell_without_line_is_kept():
    """안쪽 칸은 이웃이 받쳐 준다 — 맨 바깥이 아니면 빼지 않는다."""
    xs = [x for i, x in enumerate(BUNDLE) if i != 3]
    ridge, body = _maps(xs)
    res = decide(ridge, body, _segs(BUNDLE))
    assert res.removed == [] and not res.added


def test_loose_tube_with_body_is_added_once():
    """다발 밖 나란한 중심선(몸통 뚜렷) → 하나 더한다. 바로 옆 겹선은 하나로 친다."""
    ridge, body = _maps(BUNDLE + [230, 232])
    res = decide(ridge, body, _segs(BUNDLE))
    assert res.removed == [] and len(res.added) == 1
    a, b, w = res.added[0]
    assert 228 <= 0.5 * (a[0] + b[0]) <= 234 and w == pytest.approx(7.0)


def test_line_without_body_is_not_added():
    """다발 밑 틈 같은 가짜 중심선 — 몸통 확률이 낮으면 더하지 않는다."""
    ridge, body = _maps(BUNDLE + [230])
    body[:, 220:240] *= 0.6
    res = decide(ridge, body, _segs(BUNDLE))
    assert not res.added


def test_short_fragment_is_not_added():
    ridge, body = _maps(BUNDLE)
    r2, b2 = _maps([230], y0=100, y1=130)                   # 칸 길이의 0.19
    res = decide(np.maximum(ridge, r2), np.maximum(body, b2), _segs(BUNDLE))
    assert not res.added


# ── 배치 연동 ────────────────────────────────────────────────────────────────

class _Stub:
    def __init__(self, fn):
        self.fn = fn
        self.calls = 0

    def check(self, bgr, quads, *, ref_side=None):
        self.calls += 1
        assert ref_side == 1700                              # 돌리기 전 원본 긴 변
        return self.fn(quads)


def _light_frame():
    return _scene([0] * 5, _rows(n=5, gap=150.0, cy0=330.0))


def test_no_model_changes_nothing(monkeypatch):
    monkeypatch.delenv("AIVIS_RIDGE_MODEL", raising=False)
    b = batch_mod.inspect_batch(_light_frame(), _item(5), axis="auto", expected_count=5, stage="CUT_LENGTH")
    assert b.count_detected == 5 and b.ridge_check is None


def test_agreement_keeps_result(monkeypatch):
    stub = _Stub(lambda q: RidgeCheck())
    monkeypatch.setattr(batch_mod, "ridge_checker", lambda: stub)
    b = batch_mod.inspect_batch(_light_frame(), _item(5), axis="auto", expected_count=5, stage="CUT_LENGTH")
    assert stub.calls == 1 and b.count_detected == 5 and b.batch_verdict == "OK"
    assert b.ridge_check == {"removed": 0, "added": 0, "edge_support": [1.0, 1.0]}
    assert not any("중심선" in n for n in b.notes)


def test_removed_cell_drops_tube_and_renumbers(monkeypatch):
    stub = _Stub(lambda q: RidgeCheck(removed=[0]))
    monkeypatch.setattr(batch_mod, "ridge_checker", lambda: stub)
    b = batch_mod.inspect_batch(_light_frame(), _item(5), axis="auto", expected_count=5, stage="CUT_LENGTH")
    assert b.count_detected == 4 and b.count_mismatch and b.batch_verdict == "NG"
    assert [t.index for t in b.tubes] == [1, 2, 3, 4]
    assert b.ridge_check["removed"] == 1
    assert any("튜브 중심선 확인" in n and "뺐다" in n for n in b.notes)


def test_added_tube_counts_and_is_numbered(monkeypatch):
    def fn(quads):
        a, b, w = quad_seg(np.asarray(quads[-1]))            # 마지막 튜브 한 칸 아래(빈 바닥)에 더한다
        return RidgeCheck(added=[(a + [0, 150.0], b + [0, 150.0], w)])

    monkeypatch.setattr(batch_mod, "ridge_checker", lambda: _Stub(fn))
    b = batch_mod.inspect_batch(_light_frame(), _item(6), axis="auto", expected_count=6, stage="CUT_LENGTH")
    assert b.count_detected == 6 and not b.count_mismatch and b.ridge_check["added"] == 1
    assert [t.index for t in b.tubes] == [1, 2, 3, 4, 5, 6] and b.tubes[-1].quad is not None
    assert any("튜브 중심선 확인" in n and "더했다" in n for n in b.notes)


def test_added_tube_is_measured_like_the_others(monkeypatch):
    """더한 튜브도 띠로 펴서 잰다 — 셈의 마지막 칸을 빼고 같은 자리를 더하면 길이가 같게 나온다."""
    frame = _scene([0] * 6, _rows(n=6, gap=150.0, cy0=330.0))
    ref = batch_mod.inspect_batch(frame, _item(6), axis="auto", expected_count=6, stage="CUT_LENGTH")
    assert ref.count_detected == 6
    monkeypatch.setattr(batch_mod, "ridge_checker", lambda: _Stub(lambda q: RidgeCheck(
        removed=[len(q) - 1], added=[quad_seg(np.asarray(q[-1]))])))
    b = batch_mod.inspect_batch(frame, _item(6), axis="auto", expected_count=6, stage="CUT_LENGTH")
    assert b.count_detected == 6 and b.tubes[-1].length_mm is not None
    assert b.tubes[-1].length_mm == pytest.approx(ref.tubes[-1].length_mm, abs=0.5)


def test_dark_background_not_checked(monkeypatch):
    stub = _Stub(lambda q: RidgeCheck(removed=[0]))
    monkeypatch.setattr(batch_mod, "ridge_checker", lambda: stub)
    img = np.full((1300, 1700), 22.0)
    for i, (cx, cy) in enumerate(_rows(n=5, gap=150.0, cy0=330.0)):
        _draw_tube(img, cx, cy, 0.0, seed=100 + i)
    g = np.clip(img, 0, 255).astype(np.uint8)
    b = batch_mod.inspect_batch(np.dstack([g, g, g]), _item(5), axis="auto", expected_count=5, stage="CUT_LENGTH")
    assert stub.calls == 0 and b.ridge_check is None and b.count_detected == 5


# ── ONNX 로드(실제 미니 모델) ────────────────────────────────────────────────

def _const_model(path: Path, body_logit: float, ridge_logit: float) -> None:
    """입력 (1,3,H,W) → 출력 (1,2,H/2,W/2) 상수 로짓."""
    onnx = pytest.importorskip("onnx")
    from onnx import TensorProto, helper

    inp = helper.make_tensor_value_info("image", TensorProto.FLOAT, [1, 3, "h", "w"])
    out = helper.make_tensor_value_info("maps", TensorProto.FLOAT, [1, 2, "h2", "w2"])
    wt = helper.make_tensor("w", TensorProto.FLOAT, [2, 3, 2, 2], [0.0] * 24)
    bs = helper.make_tensor("b", TensorProto.FLOAT, [2], [body_logit, ridge_logit])
    node = helper.make_node("Conv", ["image", "w", "b"], ["maps"], kernel_shape=[2, 2], strides=[2, 2])
    g = helper.make_graph([node], "ridge_const", [inp], [out], initializer=[wt, bs])
    m = helper.make_model(g, opset_imports=[helper.make_opsetid("", 17)])
    m.ir_version = 8
    onnx.save(m, str(path))


def _bgr_and_quads():
    img = np.full((480, 640, 3), 180, np.uint8)
    quads = [np.array([[100, 60 + 20 * k], [500, 60 + 20 * k], [500, 76 + 20 * k], [100, 76 + 20 * k]], float)
             for k in range(6)]
    return img, quads


def test_onnx_checker_scales_and_decides(tmp_path):
    pytest.importorskip("onnxruntime")
    img, quads = _bgr_and_quads()
    p = tmp_path / "ridge.onnx"
    _const_model(p, 4.0, -4.0)                               # 중심선 없음 → 양쪽 맨 바깥 칸을 뺀다
    (tmp_path / "ridge.json").write_text('{"kind": "tube_ridge", "max_side": 640}', encoding="utf-8")
    res = RidgeChecker(str(p)).check(img, quads, ref_side=640)
    assert res is not None and sorted(res.removed) == [0, 5] and not res.added
    _const_model(p, 4.0, 4.0)                                # 어디나 중심선(평평) → 지지 충분, 조각 없음
    res = RidgeChecker(str(p)).check(img, quads, ref_side=1280)   # 원본이 컸던 사진(배율 0.5)
    assert res is not None and not res.changed
    assert RidgeChecker(str(p)).check(img, quads[:3]) is None     # 튜브가 적으면 보지 않는다


def test_default_checker_env(tmp_path, monkeypatch):
    pytest.importorskip("onnxruntime")
    monkeypatch.setattr(ridge_check, "_CACHE", {})
    monkeypatch.setenv("AIVIS_RIDGE_MODEL", str(tmp_path / "missing.onnx"))
    assert ridge_check.default_checker() is None
    p = tmp_path / "ridge.onnx"
    _const_model(p, 0.0, 0.0)
    monkeypatch.setenv("AIVIS_RIDGE_MODEL", str(p))
    ck = ridge_check.default_checker()
    assert isinstance(ck, RidgeChecker) and ridge_check.default_checker() is ck
    bad = tmp_path / "other.onnx"
    _const_model(bad, 0.0, 0.0)
    (tmp_path / "other.json").write_text('{"kind": "edge_cell"}', encoding="utf-8")
    monkeypatch.setenv("AIVIS_RIDGE_MODEL", str(bad))
    assert ridge_check.default_checker() is None
