"""이미지 취득 모듈 (M1) — HAL + 재시도/오류 이벤트 + 취득 타임아웃 워치독.

CLAUDE.md §6.1 / M1 DoD: 촬영 실패 시 재시도(최대 3회) 후 오류 이벤트 발행.

취득 타임아웃 워치독(grab_timeout_s):
    실 하드웨어(PiCameraAdapter 등)에서 camera.grab() 이 예외 없이 무기한
    블로킹하는 사고(예: picamera2 capture_array 가 라이브러리 내부 FIFO Job
    큐 점유로 영원히 반환하지 않는 경우)를 방어한다. grab_with_retry() 가
    camera.grab() 을 데몬 스레드에서 실행하고 Thread.join(timeout=...) 로
    대기한다 — signal 기반 타임아웃은 메인 스레드 전제·플랫폼 제약이 있고,
    worker(runner.py)가 이미 메인 스레드에 SIGTERM/SIGINT 핸들러를 설치하므로
    충돌을 피하기 위해 threading 기반으로 구현한다. 타임아웃 시 CameraError 로
    승격해 기존 재시도(max_retries) 경로에 자연스럽게 편입시키고, 스톨된 카메라
    핸들은 close() 로 버려(picamera2 내부 상태를 강제로 죽이지는 않는다 — 더
    위험하다) 다음 attempt 의 grab() 이 lazy 재오픈하도록 유도한다
    (PiCameraAdapter/GenICamCamera 는 `if not self._started/_connected:` 가드로
    이미 이 패턴을 지원한다).

    grab_timeout_s 가 None/0(기본값)이면 워치독은 완전히 비활성화되고 기존
    동기 호출과 100% 동일하게 동작한다(시뮬레이터/테스트 회귀 방지).
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import numpy as np

from .camera import (
    CameraAdapter,
    CameraError,
    GenICamCamera,
    GenICamSDKError,
    PiCameraAdapter,
    PiCameraError,
    PiCameraSDKError,
    SimulatorCamera,
    extract_strobe_config,
    map_recipe_to_genicam,
    map_recipe_to_picamera,
)
from .factory import (
    create_camera,
    create_trigger,
    get_camera_mode,
    get_trigger_mode,
)
from .trigger import (
    DigitalIOTrigger,
    FileWatchTrigger,
    MqttTrigger,
    TimerTrigger,
    TriggerSDKError,
    TriggerSource,
)

log = logging.getLogger("aivis.vision.acquisition")


@dataclass
class GrabResult:
    """취득 결과. 성공 시 frame, 실패 시 error 세팅."""

    frame: Optional[np.ndarray] = None
    attempts: int = 0
    proc_time_ms: int = 0
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.frame is not None


@dataclass
class AcquisitionService:
    """트리거 → grab 을 묶어 재시도/오류 이벤트를 관리한다 (M1).

    on_error 콜백으로 오류 이벤트를 발행한다(상위에서 sys_log/HMI 알람 연결).
    """

    camera: CameraAdapter
    max_retries: int = 3
    on_error: Optional[Callable[[str], None]] = None
    # 취득 타임아웃 워치독(초). None 또는 0 이하면 완전 비활성(기존 동기 호출과
    # 100% 동일 — 시뮬레이터/테스트 회귀 방지). 설정 시 camera.grab() 을 데몬
    # 스레드에서 실행하고 이 시간 내에 반환하지 않으면 CameraError 로 승격한다.
    grab_timeout_s: Optional[float] = None
    _events: List[str] = field(default_factory=list)
    # 워치독 관측 카운터(진행 로그·운영 모니터링용, log_every 에 노출).
    _timeout_count: int = 0
    _reconnect_count: int = 0

    def grab_with_retry(self) -> GrabResult:
        """프레임 취득. 실패 시 max_retries 회까지 재시도, 모두 실패하면 오류 이벤트."""
        t0 = time.perf_counter()
        last_err: Optional[str] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                frame = self._grab_once()
                elapsed = int(round((time.perf_counter() - t0) * 1000))
                return GrabResult(frame=frame, attempts=attempt, proc_time_ms=elapsed)
            except CameraError as exc:  # noqa: PERF203
                last_err = str(exc)
        elapsed = int(round((time.perf_counter() - t0) * 1000))
        msg = f"acquisition failed after {self.max_retries} retries: {last_err}"
        self._events.append(msg)
        if self.on_error is not None:
            self.on_error(msg)
        return GrabResult(
            frame=None, attempts=self.max_retries, proc_time_ms=elapsed, error=msg
        )

    def _grab_once(self) -> np.ndarray:
        """camera.grab() 1회 호출(타임아웃 워치독 적용, 설정 시).

        - grab_timeout_s 가 None/0 이면 기존과 완전히 동일하게 동기 호출한다
          (SimulatorCamera/GenICamCamera 스텁 포함, 회귀 없음).
        - 설정되어 있으면 camera.grab() 을 데몬 스레드에서 실행하고
          Thread.join(timeout=...) 로 대기한다. 정상 완료하면 그 결과(성공
          프레임 또는 원래 예외)를 그대로 재현한다 — CameraError 가 아닌
          예외도 원래 타입 그대로 전파해 기존 계약을 100% 보존한다.
        - 시간 내 완료하지 못하면(워치독 발동) 스레드는 데몬으로 방치한 채
          (picamera2 등 벤더 내부 상태를 강제로 죽이지 않는다 — 더 위험하다)
          카메라 핸들을 close() 로 버려 다음 attempt 의 grab() 이 새 인스턴스로
          lazy 재오픈하도록 유도하고, CameraError 로 승격해 상위 재시도
          (max_retries, 유한)에 자연스럽게 편입시킨다.
        """
        timeout = self.grab_timeout_s
        if not timeout or timeout <= 0:
            return self.camera.grab()

        outcome: dict = {}

        def _target() -> None:
            try:
                outcome["frame"] = self.camera.grab()
            except Exception as exc:  # noqa: BLE001 - 메인 스레드로 그대로 전달.
                outcome["error"] = exc

        t0 = time.perf_counter()
        th = threading.Thread(target=_target, name="aivis-camera-grab", daemon=True)
        th.start()
        th.join(timeout=timeout)

        if th.is_alive():
            elapsed = time.perf_counter() - t0
            self._timeout_count += 1
            msg = (
                f"camera grab watchdog timeout: {type(self.camera).__name__}.grab() "
                f"did not return within {elapsed:.2f}s (limit={timeout:.2f}s) — "
                "possible driver/library stall (e.g. picamera2 FIFO job queue)"
            )
            log.error("category=error acquisition_timeout: %s", msg)
            self._reconnect_count += 1
            try:
                self.camera.close()
            except Exception as close_exc:  # noqa: BLE001 - close 실패는 무해.
                log.error(
                    "category=error acquisition_reconnect: 타임아웃 후 카메라 "
                    "close 실패(무시, 다음 attempt 에서 재시도) reconnect#%d: %s",
                    self._reconnect_count,
                    close_exc,
                )
            else:
                log.error(
                    "category=error acquisition_reconnect: 타임아웃 후 카메라 "
                    "핸들 close 완료 — 다음 attempt 에서 재오픈 시도 "
                    "reconnect#%d",
                    self._reconnect_count,
                )
            # 워치독 스레드 자체는 종료를 강제하지 않고 데몬으로 방치한다.
            raise CameraError(msg)

        if "error" in outcome:
            raise outcome["error"]
        return outcome["frame"]

    @property
    def timeout_count(self) -> int:
        """취득 워치독 타임아웃 발생 누적 횟수(관측성, log_every 에 노출)."""
        return self._timeout_count

    @property
    def reconnect_count(self) -> int:
        """타임아웃 후 카메라 핸들 close(재연결 유도) 시도 누적 횟수."""
        return self._reconnect_count

    @property
    def error_events(self) -> List[str]:
        return list(self._events)


__all__ = [
    "AcquisitionService",
    "GrabResult",
    "CameraAdapter",
    "CameraError",
    "SimulatorCamera",
    "GenICamCamera",
    "GenICamSDKError",
    "PiCameraAdapter",
    "PiCameraError",
    "PiCameraSDKError",
    "map_recipe_to_genicam",
    "map_recipe_to_picamera",
    "extract_strobe_config",
    "TriggerSource",
    "TimerTrigger",
    "FileWatchTrigger",
    "DigitalIOTrigger",
    "MqttTrigger",
    "TriggerSDKError",
    "create_camera",
    "create_trigger",
    "get_camera_mode",
    "get_trigger_mode",
]
