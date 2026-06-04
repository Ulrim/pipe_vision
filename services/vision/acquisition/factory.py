"""카메라/트리거 팩토리 — AIVIS_CAMERA 환경변수로 어댑터 선택 (CLAUDE.md §6.1).

AIVIS_CAMERA=sim     → SimulatorCamera (기본)
AIVIS_CAMERA=genicam → GenICamCamera (P7 통합 단계)
"""
from __future__ import annotations

import os
from typing import Optional

from .camera import CameraAdapter, GenICamCamera, SimulatorCamera
from .trigger import TimerTrigger, TriggerSource


def get_camera_mode() -> str:
    return os.environ.get("AIVIS_CAMERA", "sim").strip().lower()


def create_camera(
    dataset_dir: Optional[str] = None,
    view_filter: Optional[str] = None,
    loop: bool = True,
) -> CameraAdapter:
    """AIVIS_CAMERA 에 따라 카메라 어댑터를 생성한다."""
    mode = get_camera_mode()
    if mode == "sim":
        return SimulatorCamera(
            dataset_dir=dataset_dir, view_filter=view_filter, loop=loop
        )
    if mode == "genicam":
        return GenICamCamera()
    raise ValueError(
        f"AIVIS_CAMERA={mode!r} 미지원. 'sim' 또는 'genicam' 을 사용하라."
    )


def create_trigger(interval_s: float = 0.0) -> TriggerSource:
    """기본 트리거(시뮬레이터 타이머). 실물은 P7 에서 IO/MQTT 로 교체."""
    mode = get_camera_mode()
    if mode == "sim":
        return TimerTrigger(interval_s=interval_s)
    # genicam 모드의 실 트리거는 P7 에서 DigitalIOTrigger/MqttTrigger 로 결선.
    return TimerTrigger(interval_s=interval_s)
