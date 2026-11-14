"""맨 바깥 칸 확인(edge_check.py) — 칸 잘라 내기 방향, 배치 연동(재확인만, 개수는 그대로), 모델 로드 (2026-11-14).

모델 자체(고객 사진으로 학습)는 저장소에 없다. 여기서는 잘라 내기가 '바깥이 위' 인지, 모델이 셈과 다를 때
개수를 고치지 않고 재확인으로 돌리는지, 모델이 없거나 어두운 배경이면 아무것도 바꾸지 않는지만 본다.
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
from vision.multi import edge_check  # noqa: E402
from vision.multi.edge_check import CELL_H, CELL_W, KINDS, EdgeCheck, EdgeChecker, cell_crops  # noqa: E402

from .test_light_background import _draw_tube, _item, _rows, _scene  # noqa: E402


def _stack_image(n=6, pitch=20, x0=40, x1=440, y0=100, bg=180.0):
    """일정한 바닥 위 붙은 가로 튜브 n 개(반사 띠가 있는 단면) + 그 사각형들."""
    h, w = 400, 480
    img = np.full((h, w), bg, np.float32)
    quads = []
    for k in range(n):
        top = y0 + k * pitch
        v = (np.arange(pitch) + 0.5) / pitch
        prof = 60.0 + 150.0 * np.exp(-((v - 0.4) / 0.2) ** 2)
        img[top:top + pitch, x0:x1] = prof[:, None]
        quads.append(np.array([[x0, top], [x1, top], [x1, top + pitch], [x0, top + pitch]], np.float64))
    return img.astype(np.uint8), quads


def test_cells_put_outside_on_top():
    img, quads = _stack_image()
    cells, names = cell_crops(img, quads)
    assert names == list(KINDS)
    assert cells.shape == (4, CELL_H, CELL_W)
    q = CELL_H // 4
    for i, name in enumerate(names):
        top, bottom = cells[i, :q], cells[i, -q:]
        # 위 1/4(바깥 반 칸)는 고른 바닥, 아래 1/4 는 튜브 단면(반사 띠) — 바깥 칸(out)은 아래가 맨 끝 튜브의 바깥 반쪽
        assert float(top.std()) < 0.05, name
        assert float(bottom.std()) > 0.2, name


def test_cells_ignore_quad_direction_and_order():
    """사각형 꼭짓점 방향(왼→오/오→왼)과 튜브 순서가 섞여도 같은 네 칸."""
    img, quads = _stack_image()
    flipped = [q[[1, 0, 3, 2]] if i % 2 else q for i, q in enumerate(quads)]
    shuffled = [flipped[i] for i in (3, 0, 5, 1, 4, 2)]
    a, _ = cell_crops(img, quads)
    b, _ = cell_crops(img, shuffled)
    # 길이 방향 좌우는 바뀔 수 있다(모델은 좌우 뒤집은 칸과 평균한다)
    for i in range(4):
        same = np.allclose(a[i], b[i], atol=1e-3) or np.allclose(a[i], b[i][:, ::-1], atol=1e-3)
        assert same, KINDS[i]


def test_too_few_tubes_skipped():
    img, quads = _stack_image(n=3)
    assert cell_crops(img, quads) is None


class _Stub:
    def __init__(self, check):
        self.result = check
        self.calls = 0

    def check(self, gray, quads):
        self.calls += 1
        assert len(quads) >= edge_check.MIN_TUBES
        return self.result


def _light_frame():
    return _scene([0] * 5, _rows(n=5, gap=150.0, cy0=330.0))


def test_no_model_changes_nothing(monkeypatch):
    monkeypatch.delenv("AIVIS_EDGE_MODEL", raising=False)
    b = batch_mod.inspect_batch(_light_frame(), _item(5), axis="auto", expected_count=5, stage="CUT_LENGTH")
    assert b.count_detected == 5 and b.edge_check is None
    assert not b.count_uncertain


def test_agreement_keeps_result(monkeypatch):
    stub = _Stub(EdgeCheck(0.99, 0.01, 0.98, 0.02))
    monkeypatch.setattr(batch_mod, "edge_checker", lambda: stub)
    b = batch_mod.inspect_batch(_light_frame(), _item(5), axis="auto", expected_count=5, stage="CUT_LENGTH")
    assert stub.calls == 1
    assert b.count_detected == 5 and not b.count_uncertain and b.batch_verdict == "OK"
    assert b.edge_check is not None and b.edge_check["disagree"] is False


def test_disagreement_sends_to_review_without_changing_count(monkeypatch):
    stub = _Stub(EdgeCheck(0.99, 0.01, 0.12, 0.02))       # 한쪽 맨 끝 칸이 튜브로 안 보임
    monkeypatch.setattr(batch_mod, "edge_checker", lambda: stub)
    b = batch_mod.inspect_batch(_light_frame(), _item(5), axis="auto", expected_count=5, stage="CUT_LENGTH")
    assert b.count_detected == 5                           # 개수는 고치지 않는다
    assert b.count_uncertain and b.batch_verdict == "NG"
    assert all(t.review_flag for t in b.tubes)
    assert any("맨 바깥 칸 확인" in n for n in b.notes)
    assert b.edge_check["disagree"] is True


def test_dark_background_not_checked(monkeypatch):
    """학습한 영역(밝은 바닥) 밖 — 현장 권장 무광 검정 바닥에서는 모델을 보지 않는다."""
    stub = _Stub(EdgeCheck(0.0, 1.0, 0.0, 1.0))
    monkeypatch.setattr(batch_mod, "edge_checker", lambda: stub)
    img = np.full((1300, 1700), 22.0)                       # 무광 검정 바닥
    for i, (cx, cy) in enumerate(_rows(n=5, gap=150.0, cy0=330.0)):
        _draw_tube(img, cx, cy, 0.0, seed=100 + i)
    g = np.clip(img, 0, 255).astype(np.uint8)
    frame = np.dstack([g, g, g])
    b = batch_mod.inspect_batch(frame, _item(5), axis="auto", expected_count=5, stage="CUT_LENGTH")
    assert stub.calls == 0 and b.edge_check is None


def test_note_names_the_side():
    ec = EdgeCheck(0.1, 0.9, 0.99, 0.01)
    assert ec.disagree
    assert "맨 끝 칸" in ec.note() and "바깥 한 칸" in ec.note()


# ── ONNX 로드(실제 미니 모델) ────────────────────────────────────────────────

def _const_model(path: Path, logit: float) -> None:
    onnx = pytest.importorskip("onnx")
    from onnx import TensorProto, helper

    inp = helper.make_tensor_value_info("cells", TensorProto.FLOAT, ["n", 1, CELL_H, CELL_W])
    out = helper.make_tensor_value_info("logit", TensorProto.FLOAT, ["n"])
    zero = helper.make_tensor("zero", TensorProto.FLOAT, [], [0.0])
    bias = helper.make_tensor("bias", TensorProto.FLOAT, [], [logit])
    nodes = [helper.make_node("ReduceMean", ["cells"], ["m"], axes=[1, 2, 3], keepdims=0),
             helper.make_node("Mul", ["m", "zero"], ["z"]),
             helper.make_node("Add", ["z", "bias"], ["logit"])]
    g = helper.make_graph(nodes, "edge_const", [inp], [out], initializer=[zero, bias])
    m = helper.make_model(g, opset_imports=[helper.make_opsetid("", 17)])
    m.ir_version = 8
    onnx.save(m, str(path))


def test_onnx_checker_loads_and_flags(tmp_path):
    pytest.importorskip("onnxruntime")
    p = tmp_path / "edge.onnx"
    _const_model(p, 4.0)                                    # 모든 칸 '튜브' → 바깥 칸도 튜브 → 불일치
    (tmp_path / "edge.json").write_text('{"kind": "edge_cell", "threshold": 0.5}', encoding="utf-8")
    ck = EdgeChecker(str(p))
    img, quads = _stack_image()
    ec = ck.check(img, quads)
    assert ec is not None and ec.p_lo_edge > 0.95 and ec.p_hi_out > 0.95 and ec.disagree


def test_default_checker_env(tmp_path, monkeypatch):
    pytest.importorskip("onnxruntime")
    monkeypatch.setattr(edge_check, "_CACHE", {})
    monkeypatch.setenv("AIVIS_EDGE_MODEL", str(tmp_path / "missing.onnx"))
    assert edge_check.default_checker() is None             # 파일 없음 → 끔
    p = tmp_path / "edge.onnx"
    _const_model(p, -4.0)
    monkeypatch.setenv("AIVIS_EDGE_MODEL", str(p))
    ck = edge_check.default_checker()
    assert isinstance(ck, EdgeChecker) and edge_check.default_checker() is ck   # 한 번만 연다
    bad = tmp_path / "other.onnx"
    _const_model(bad, 0.0)
    (tmp_path / "other.json").write_text('{"kind": "surface3"}', encoding="utf-8")
    monkeypatch.setenv("AIVIS_EDGE_MODEL", str(bad))
    assert edge_check.default_checker() is None             # 다른 종류 모델 → 끔(조용히)
