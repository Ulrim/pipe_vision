"""M1 취득/HAL 테스트 (AIVIS_CAMERA=sim)."""
from __future__ import annotations


import numpy as np
import pytest

from vision.acquisition import (
    AcquisitionService,
    CameraError,
    SimulatorCamera,
    create_camera,
    get_camera_mode,
)
from vision.acquisition.camera import GenICamCamera


def test_factory_default_sim(monkeypatch, dataset_dir):
    monkeypatch.delenv("AIVIS_CAMERA", raising=False)
    assert get_camera_mode() == "sim"
    cam = create_camera(dataset_dir=str(dataset_dir), view_filter="SIDE")
    assert isinstance(cam, SimulatorCamera)


def test_simulator_replays_sequentially_and_loops(dataset_dir):
    cam = SimulatorCamera(dataset_dir=str(dataset_dir), view_filter="SIDE")
    n = len(cam.files)
    assert n > 0
    first = cam.grab()
    assert isinstance(first, np.ndarray) and first.ndim == 3
    # 순환: n+1 번째는 첫 이미지와 동일.
    for _ in range(n - 1):
        cam.grab()
    looped = cam.grab()
    assert np.array_equal(first, looped)


def test_view_filter_selects_side(dataset_dir):
    cam = SimulatorCamera(dataset_dir=str(dataset_dir), view_filter="SIDE")
    assert all("_SIDE_" in p.name.upper() for p in cam.files)


def test_grab_under_50ms(dataset_dir):
    import time

    cam = SimulatorCamera(dataset_dir=str(dataset_dir), view_filter="SIDE")
    t0 = time.perf_counter()
    cam.grab()
    ms = (time.perf_counter() - t0) * 1000
    assert ms <= 50.0, f"grab took {ms:.1f}ms (>50ms target)"


class _FlakyCamera(SimulatorCamera):
    """앞 k회 grab 실패 후 정상."""

    def __init__(self, fail_times, **kw):
        super().__init__(**kw)
        self._fail = fail_times

    def grab(self):
        if self._fail > 0:
            self._fail -= 1
            raise CameraError("synthetic transient failure")
        return super().grab()


def test_retry_recovers(dataset_dir):
    cam = _FlakyCamera(2, dataset_dir=str(dataset_dir), view_filter="SIDE")
    svc = AcquisitionService(camera=cam, max_retries=3)
    res = svc.grab_with_retry()
    assert res.ok
    assert res.attempts == 3


def test_retry_exhausted_emits_error(dataset_dir):
    events = []
    cam = _FlakyCamera(5, dataset_dir=str(dataset_dir), view_filter="SIDE")
    svc = AcquisitionService(camera=cam, max_retries=3, on_error=events.append)
    res = svc.grab_with_retry()
    assert not res.ok
    assert res.error is not None
    assert len(events) == 1  # 오류 이벤트 1회 발행(M1 DoD)


def test_genicam_stub_raises():
    cam = GenICamCamera()
    with pytest.raises(NotImplementedError):
        cam.grab()
