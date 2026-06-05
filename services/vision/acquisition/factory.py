"""카메라/트리거 팩토리 — AIVIS_CAMERA 환경변수로 어댑터 선택 (CLAUDE.md §6.1).

AIVIS_CAMERA=sim     → SimulatorCamera (기본)
AIVIS_CAMERA=genicam → GenICamCamera (P7 통합 단계)
"""
from __future__ import annotations

import os
from typing import Optional

from .camera import CameraAdapter, GenICamCamera, SimulatorCamera
from .trigger import (
    DigitalIOTrigger,
    MqttTrigger,
    TimerTrigger,
    TriggerSource,
)


def get_camera_mode() -> str:
    return os.environ.get("AIVIS_CAMERA", "sim").strip().lower()


def get_trigger_mode() -> str:
    """실 트리거 소스 선택(genicam 모드 한정). 기본은 timer.

    AIVIS_TRIGGER=timer|filewatch|dio|mqtt.
    sim 모드에서는 항상 timer/filewatch(시뮬레이터)만 사용한다.
    """
    return os.environ.get("AIVIS_TRIGGER", "timer").strip().lower()


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
        # 생성은 항상 성공한다(SDK 미설치여도). 실 디바이스 접근은
        # configure/grab 시점에 SDK/환경을 요구한다(GenICamSDKError).
        return GenICamCamera()
    raise ValueError(
        f"AIVIS_CAMERA={mode!r} 미지원. 'sim' 또는 'genicam' 을 사용하라."
    )


def create_trigger(interval_s: float = 0.0) -> TriggerSource:
    """트리거 소스 생성.

    - sim 모드: 항상 TimerTrigger(시뮬레이터). 결정적·외부의존 없음.
    - genicam 모드: AIVIS_TRIGGER 로 실 트리거 선택(dio/mqtt). 생성은 항상
      성공하고, wait_for_trigger 시점에 드라이버/SDK 를 요구한다(안내 예외).
    """
    mode = get_camera_mode()
    if mode == "sim":
        return TimerTrigger(interval_s=interval_s)
    # genicam 모드: 실 트리거 결선(생성은 성공, 대기 시 SDK/드라이버 필요).
    tmode = get_trigger_mode()
    if tmode == "dio":
        return DigitalIOTrigger()
    if tmode == "mqtt":
        return MqttTrigger()
    # timer/filewatch 등은 시뮬 트리거로 폴백(개발/계측용).
    return TimerTrigger(interval_s=interval_s)
