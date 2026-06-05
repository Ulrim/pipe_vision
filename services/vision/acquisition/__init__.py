"""이미지 취득 모듈 (M1) — HAL + 재시도/오류 이벤트.

CLAUDE.md §6.1 / M1 DoD: 촬영 실패 시 재시도(최대 3회) 후 오류 이벤트 발행.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import numpy as np

from .camera import (
    CameraAdapter,
    CameraError,
    GenICamCamera,
    GenICamSDKError,
    SimulatorCamera,
    extract_strobe_config,
    map_recipe_to_genicam,
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
    _events: List[str] = field(default_factory=list)

    def grab_with_retry(self) -> GrabResult:
        """프레임 취득. 실패 시 max_retries 회까지 재시도, 모두 실패하면 오류 이벤트."""
        t0 = time.perf_counter()
        last_err: Optional[str] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                frame = self.camera.grab()
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
    "map_recipe_to_genicam",
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
