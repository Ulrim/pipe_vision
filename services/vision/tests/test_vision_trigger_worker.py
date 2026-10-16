"""영상 트리거 + 실제 워커 (2026-10-16).

1. 길이 스테이션: 다발마다 한 번 찍혀 튜브 N행이 올라간다. 빈 장면에 쏘면 아무것도
   올리지 않고('미검출' 경고도 없이) 빈 장면으로 배운다.
2. 크레이트 스테이션: 채우는 동안 여러 번 조용해져도 **판당 개수 1건**, 빠지기 직전
   가장 꽉 찬 사진으로 센다. 검사 시각은 그 사진을 찍은 시각이다.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import cv2
import numpy as np

_SERVICES_DIR = Path(__file__).resolve().parents[2]
if str(_SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_DIR))

from vision.acquisition import AcquisitionService, VisionTrigger  # noqa: E402
from vision.acquisition.camera import _to_preview  # noqa: E402
from vision.multi.bundle import count_bundle  # noqa: E402
from vision.tools.gen_synthetic import make_multi_image  # noqa: E402
from vision.worker.runner import Worker  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "_test_worker_stubs_vt", Path(__file__).with_name("test_worker.py"))
_tw = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_tw)
FakeBackend, _cfg, _client = _tw.FakeBackend, _tw._cfg, _tw._client


class FrameCam:
    """고해상도 장면 대본. preview 는 그 장면을 줄인 흑백, grab 은 그 장면."""

    def __init__(self, frames):
        self.frames = list(frames)
        self.i = 0
        self.cur = self.frames[0]

    def preview(self, width=320):
        self.cur = self.frames[min(self.i, len(self.frames) - 1)]
        self.i += 1
        return _to_preview(self.cur, width)

    def grab(self):
        return self.cur.copy()

    def configure(self, recipe):
        pass

    def close(self):
        pass


def _worker(tmp_path, cam, expected):
    backend = FakeBackend(master_requires_auth=True)
    w = Worker(_cfg(tmp_path, max_iterations=1), client=_client(backend))
    assert w.startup() is True
    w.item = w.item.model_copy(update={"expected_count": expected})
    w.camera = cam
    w.acq = AcquisitionService(camera=cam, max_retries=1, grab_timeout_s=5)
    w.trigger = VisionTrigger(cam, mode_provider=w._vision_mode, fps=10, settle_ms=300,
                              depart_settle_ms=500, preview_width=160)
    return w, backend


def _drive(w, cam, steps):
    t = 0.0
    w.trigger._clock = lambda: t
    shots = 0
    for _ in range(steps):
        t += 0.1
        if w.trigger.poll_once():
            shots += 1
            w.run_once()
    return shots


def test_length_station_one_shot_per_bundle_and_empty_learned(tmp_path):
    full, _ = make_multi_image(10)
    dark = np.full_like(full, 12)
    h, w_ = full.shape[:2]

    def sliding(shift):
        out = np.full_like(full, 12)
        out[:, shift:] = full[:, : w_ - shift]
        return out

    one_cycle = ([dark] * 6 + [sliding(int(w_ * f)) for f in (0.8, 0.5, 0.2)]
                 + [full] * 8 + [sliding(int(w_ * 0.6))])
    cam = FrameCam(one_cycle * 3 + [dark] * 6)
    w, backend = _worker(tmp_path, cam, expected=10)
    shots = _drive(w, cam, len(cam.frames))

    rows = backend.posted
    frames = sorted({r["inspected_at"] for r in rows})
    assert len(frames) == 3, f"다발 3개 → 사진 3장이어야 한다(촬영 {shots}회)"
    assert len(rows) == 30 and {r["tube_index"] for r in rows} == set(range(10))
    # 빈 장면에 쏜 것은 올리지 않고 '제품 대기' 하트비트만, 빈 장면을 배웠다.
    assert shots == 4, "기동 직후 빈 화면 1회 + 다발 3회"
    assert w.trigger.empty_scenes == 1
    assert any(s.get("waiting") for s in backend.statuses)
    assert not any(s.get("detected") == 0 and s.get("mismatch") for s in backend.statuses), \
        "빈 장면을 '미검출' 경고로 띄우면 안 된다"
    w.shutdown()


def crate_fill(k, cols=8, rows=4, pitch=60, r_bore=14):
    """고정 크기 크레이트에 단면 k 개(위에서 본 모습)."""
    hgt, wid = rows * pitch + 80, cols * pitch + 80
    img = np.full((hgt, wid, 3), 190, np.uint8)
    n = 0
    for i in range(rows):
        for j in range(cols):
            if n >= k:
                return img
            cx, cy = 40 + j * pitch + pitch // 2, 40 + i * pitch + pitch // 2
            cv2.circle(img, (cx, cy), r_bore + 10, (215, 215, 215), -1)
            cv2.circle(img, (cx, cy), r_bore, (25, 25, 25), -1)
            n += 1
    return img


def test_synthetic_crate_counts():
    assert count_bundle(crate_fill(32)).count == 32


def test_crate_station_counts_once_with_full_crate(tmp_path):
    def hand(img):
        out = img.copy()
        out[60:260, 100:420] = 200
        return out

    frames = []
    for k in (8, 16, 24, 32):
        frames += [hand(crate_fill(k))] * 3 + [crate_fill(k)] * 8
    frames += [hand(crate_fill(32))] * 2 + [np.full_like(crate_fill(0), 70)] * 10
    cam = FrameCam(frames)
    w, backend = _worker(tmp_path, cam, expected=32)
    w._apply_active_meta({"inspection_stage": "CRATE_COUNT"})
    assert w.trigger.mode() == "departure"
    _drive(w, cam, len(frames))

    rows = [r for r in backend.posted if r["inspection_stage"] == "CRATE_COUNT"]
    assert len(rows) == 1, f"한 판이 {len(rows)}번 세어졌다(MES 실적이 부풀려진다)"
    assert rows[0]["count_detected"] == 32 and rows[0]["final_verdict"] == "OK"
    w.shutdown()


def test_run_loop_with_sim_and_env(tmp_path, monkeypatch):
    """AIVIS_TRIGGER=vision 만으로 실제 루프가 돈다(팩토리 → 가상 컨베이어 → 판정 → 전송)."""
    monkeypatch.setenv("AIVIS_TRIGGER", "vision")
    monkeypatch.setenv("AIVIS_VISION_FPS", "400")
    monkeypatch.setenv("AIVIS_VISION_SETTLE_MS", "10")
    backend = FakeBackend(master_requires_auth=True)
    w = Worker(_cfg(tmp_path, max_iterations=4), client=_client(backend))
    assert w.run() == 0
    assert isinstance(w.trigger, VisionTrigger)
    assert backend.posted, "가상 컨베이어의 제품을 찍어 올렸어야 한다"
    # 빈 화면에 쏜 것은 올라가지 않는다 — 올라간 행은 전부 실제 사진(경로 있음)이다.
    assert all(r.get("result_image_path") for r in backend.posted)
    assert w.trigger.empty_scenes >= 1
