"""처리시간 = 이미지 취득 ~ 결과 저장(§1.2 지표3) — 2026-10-10 점검 보완.

종전에는 판정 구간만 재서 취득·이미지 저장 시간이 빠졌다(PC 합성 12ms vs 파이
실해상도 판정만 191ms). 행의 proc_time_ms 는 이제 사이클 전체이고, 하트비트에
단계별 분해(grab/infer/save/post)가 실린다.
"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import numpy as np

_SERVICES_DIR = Path(__file__).resolve().parents[2]
if str(_SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_DIR))

from vision.acquisition import GrabResult  # noqa: E402
from vision.worker.runner import Worker  # noqa: E402
from vision.worker.timing import CycleClock, per_ea_ms  # noqa: E402

_spec = importlib.util.spec_from_file_location("_tw_timing", Path(__file__).with_name("test_worker.py"))
_tw = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_tw)


class _Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def test_cycle_clock_breakdown_sums_to_total():
    c = _Clock()
    k = CycleClock(clock=c)
    c.t += 0.040
    k.mark("grab")
    c.t += 0.191
    k.mark("infer")
    c.t += 0.085
    k.mark("save")
    b = k.breakdown(post_ms=7)
    assert (b["grab_ms"], b["infer_ms"], b["save_ms"]) == (40, 191, 85)
    assert b["total_ms"] == 316 == b["grab_ms"] + b["infer_ms"] + b["save_ms"]
    assert b["post_ms"] == 7 and b["n"] == 1 and b["per_ea_ms"] == 316


def test_bundle_per_ea_is_frame_over_count():
    assert per_ea_ms(600, 20) == 30
    assert per_ea_ms(600, 0) == 600
    c = _Clock()
    k = CycleClock(clock=c)
    c.t += 0.6
    k.mark("save")
    assert k.breakdown(n=20)["per_ea_ms"] == 30


class _SlowAcq:
    """취득에 시간이 걸리는 카메라 — 그 시간이 처리시간에 들어가야 한다."""

    def __init__(self, frame: np.ndarray, delay_s: float) -> None:
        self._frame, self._delay = frame, delay_s

    def grab_with_retry(self) -> GrabResult:
        time.sleep(self._delay)
        return GrabResult(frame=self._frame, attempts=1, proc_time_ms=int(self._delay * 1000))


def test_worker_row_time_includes_acquisition_and_save(tmp_path):
    backend = _tw.FakeBackend()
    w = Worker(_tw._cfg(tmp_path, max_iterations=1, images_dir=str(tmp_path / "img")),
               client=_tw._client(backend))
    assert w.startup() is True
    f = np.full((240, 640, 3), 30, np.uint8)
    f[100:140, 120:520] = 220
    w.acq = _SlowAcq(f, 0.05)
    w.run_once()
    row = backend.posted[-1]
    st = backend.statuses[-1]
    t = st["timings"]
    assert t["grab_ms"] >= 45, "취득 시간이 빠지면 안 된다"
    assert {"infer_ms", "save_ms", "total_ms"} <= set(t)
    assert row["proc_time_ms"] == t["total_ms"] >= t["grab_ms"] + t["infer_ms"]
    assert st["proc_time_ms"] == row["proc_time_ms"]
    # 두 번째 사이클 하트비트에는 첫 사이클의 결과 전송 시간이 실린다.
    w.run_once()
    assert "post_ms" in backend.statuses[-1]["timings"]
    w.shutdown()
