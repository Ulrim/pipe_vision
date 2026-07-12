"""M1 취득/HAL 테스트 (AIVIS_CAMERA=sim)."""
from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from vision.acquisition import (
    AcquisitionService,
    CameraAdapter,
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


# =====================================================================
# 취득 타임아웃 워치독 (grab_timeout_s) — 실 하드웨어(PiCameraAdapter 등)
# 에서 camera.grab() 이 예외 없이 무기한 블로킹하는 사고를 재현/검증한다.
# AIVIS_CAMERA=sim 으로는 재현 불가하므로, grab() 이 threading.Event().wait()
# (타임아웃 없음)로 절대 반환하지 않는 가짜 CameraAdapter 를 사용한다.
# 워치독 스레드는 데몬으로 방치되므로 프로세스/pytest 종료를 막지 않는다.
# =====================================================================


class _NeverReturnsCamera(CameraAdapter):
    """grab() 이 영원히 블로킹(예외도 반환도 없음). close() 는 카운트만 한다."""

    def __init__(self) -> None:
        self.close_calls = 0
        self._never = threading.Event()  # 절대 set() 되지 않음 → 영구 블로킹.

    def configure(self, recipe: dict) -> None:
        pass

    def grab(self) -> np.ndarray:
        self._never.wait()  # 타임아웃 없이 대기 — 실 하드웨어 스톨 재현.
        return np.zeros((2, 2, 3), dtype=np.uint8)  # pragma: no cover (도달 안 함)

    def close(self) -> None:
        self.close_calls += 1


def test_grab_timeout_default_is_disabled():
    """grab_timeout_s 기본값은 None(비활성) — 하위호환 보장."""
    cam = SimulatorCamera()
    svc = AcquisitionService(camera=cam)
    assert svc.grab_timeout_s is None
    assert svc.timeout_count == 0
    assert svc.reconnect_count == 0


def test_grab_timeout_disabled_never_spawns_thread(dataset_dir, monkeypatch):
    """grab_timeout_s 미설정 시 기존과 완전히 동일(동기 호출, 스레드 미사용)."""
    import vision.acquisition as acq_mod

    cam = SimulatorCamera(dataset_dir=str(dataset_dir), view_filter="SIDE")
    svc = AcquisitionService(camera=cam, max_retries=3)  # grab_timeout_s=None(기본).

    def _boom(*_a, **_k):
        raise AssertionError("워치독 비활성 상태에서 스레드를 생성하면 안 된다")

    monkeypatch.setattr(acq_mod.threading, "Thread", _boom)
    res = svc.grab_with_retry()
    assert res.ok
    assert svc.timeout_count == 0


def test_grab_timeout_zero_is_also_disabled(dataset_dir):
    """grab_timeout_s=0 도 None 과 동일하게 비활성 취급된다."""
    cam = SimulatorCamera(dataset_dir=str(dataset_dir), view_filter="SIDE")
    svc = AcquisitionService(camera=cam, max_retries=3, grab_timeout_s=0)
    res = svc.grab_with_retry()
    assert res.ok
    assert svc.timeout_count == 0


def test_grab_once_raises_cameraerror_on_watchdog_timeout():
    """워치독이 지정 시간(±여유) 내에 반드시 CameraError 를 던진다."""
    cam = _NeverReturnsCamera()
    svc = AcquisitionService(camera=cam, max_retries=1, grab_timeout_s=0.2)
    t0 = time.perf_counter()
    with pytest.raises(CameraError) as ei:
        svc._grab_once()
    elapsed = time.perf_counter() - t0
    assert "watchdog timeout" in str(ei.value)
    assert elapsed < 2.0, f"워치독이 제때 반환하지 않음: {elapsed:.2f}s"
    assert svc.timeout_count == 1
    # 스톨된 핸들은 버려진다(다음 attempt 가 재오픈하도록).
    assert cam.close_calls == 1
    assert svc.reconnect_count == 1


def test_grab_watchdog_reconnect_then_next_attempt_succeeds():
    """타임아웃 후 close() 로 카메라가 회복되면 다음 attempt 는 정상 취득한다."""

    class _HangThenRecover(CameraAdapter):
        def __init__(self) -> None:
            self.hang = True
            self._never = threading.Event()
            self.close_calls = 0

        def configure(self, recipe: dict) -> None:
            pass

        def grab(self) -> np.ndarray:
            if self.hang:
                self._never.wait()
            return np.full((2, 2, 3), 7, dtype=np.uint8)

        def close(self) -> None:
            self.close_calls += 1
            self.hang = False  # 재연결(재오픈) 성공을 흉내낸다.

    cam = _HangThenRecover()
    svc = AcquisitionService(camera=cam, max_retries=3, grab_timeout_s=0.2)
    res = svc.grab_with_retry()
    assert res.ok
    assert res.attempts == 2  # 1회 타임아웃(재연결) 후 2회차 성공.
    assert cam.close_calls == 1
    assert svc.timeout_count == 1
    assert svc.reconnect_count == 1
    assert np.array_equal(res.frame, np.full((2, 2, 3), 7, dtype=np.uint8))


def test_grab_watchdog_exhausted_retries_returns_failed_result_no_infinite_loop():
    """반복 타임아웃 시 max_retries 를 넘지 않고(무한 재시도 금지) 명확히 실패한다."""
    events = []
    cam = _NeverReturnsCamera()
    svc = AcquisitionService(
        camera=cam, max_retries=2, grab_timeout_s=0.1, on_error=events.append
    )
    res = svc.grab_with_retry()
    assert res.ok is False
    assert res.error is not None
    assert "watchdog timeout" in res.error
    assert res.attempts == 2
    assert svc.timeout_count == 2  # max_retries 를 넘지 않음(유한).
    assert cam.close_calls == 2
    assert len(events) == 1  # 복구불가 오류 이벤트 1회 발행.


def test_grab_watchdog_normal_fast_camera_no_overhead(dataset_dir):
    """정상(빠른) grab() 은 워치독이 활성화돼 있어도 지연/부작용이 없다."""
    cam = SimulatorCamera(dataset_dir=str(dataset_dir), view_filter="SIDE")
    svc = AcquisitionService(camera=cam, max_retries=3, grab_timeout_s=2.0)
    t0 = time.perf_counter()
    res = svc.grab_with_retry()
    ms = (time.perf_counter() - t0) * 1000
    assert res.ok
    assert ms < 200.0, f"grab took {ms:.1f}ms with watchdog enabled(과도한 지연)"
    assert svc.timeout_count == 0
    assert svc.reconnect_count == 0


def test_grab_watchdog_preserves_non_cameraerror_exception_type():
    """CameraError 가 아닌 예외는 승격하지 않고 원래 타입 그대로 전파한다."""

    class _BuggyCamera(CameraAdapter):
        def configure(self, recipe: dict) -> None:
            pass

        def grab(self) -> np.ndarray:
            raise ValueError("not a CameraError")

        def close(self) -> None:
            pass

    svc = AcquisitionService(camera=_BuggyCamera(), max_retries=3, grab_timeout_s=1.0)
    with pytest.raises(ValueError):
        svc.grab_with_retry()
