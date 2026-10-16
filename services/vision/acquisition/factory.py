"""카메라/트리거 팩토리 — AIVIS_CAMERA 환경변수로 어댑터 선택 (CLAUDE.md §6.1).

AIVIS_CAMERA=sim     → SimulatorCamera (기본)
AIVIS_CAMERA=genicam → GenICamCamera (GigE/USB3 Vision, P7 통합 단계)
AIVIS_CAMERA=picam   → PiCameraAdapter (Raspberry Pi Camera v3/IMX708, 최종 배포 HW)
"""
from __future__ import annotations

import os
from typing import Optional

from .camera import CameraAdapter, GenICamCamera, PiCameraAdapter, SimulatorCamera
from .trigger import (
    DigitalIOTrigger,
    GpioTrigger,
    MqttTrigger,
    TimerTrigger,
    TriggerSource,
    VisionTrigger,
)


def get_camera_mode() -> str:
    return os.environ.get("AIVIS_CAMERA", "sim").strip().lower()


def get_trigger_mode() -> str:
    """실 트리거 소스 선택(genicam 모드 한정). 기본은 timer.

    AIVIS_TRIGGER=timer|filewatch|dio|mqtt|gpio|vision
    (gpio = 라즈베리파이 근접센서, vision = 카메라 영상으로 판단 — 2026-10-16).
    sim 모드에서는 timer, 또는 vision(가상 컨베이어 장면)만 쓴다.
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
    if mode == "picam":
        # Raspberry Pi Camera v3(IMX708). 생성은 항상 성공한다(picamera2
        # 미설치여도). 실 디바이스 접근은 configure/grab 시점에 picamera2 를
        # 요구한다(PiCameraSDKError).
        return PiCameraAdapter()
    raise ValueError(
        f"AIVIS_CAMERA={mode!r} 미지원. 'sim' | 'genicam' | 'picam' 을 사용하라."
    )


def create_trigger(interval_s: float = 0.0, *, camera: Optional[CameraAdapter] = None,
                   mode_provider=None, roi: Optional[tuple] = None) -> TriggerSource:
    """트리거 소스 생성.

    - AIVIS_TRIGGER=vision(모든 카메라 모드): VisionTrigger — 카메라 미리보기로 도착·정지·
      반출을 보고 찍는다. camera 가 필요하다(워커가 넘긴다). mode_provider() 가
      "arrival"|"departure" 를 돌려주면 검사 모드에 따라 자동 전환(크레이트 = 반출).
    - sim 모드: 그 밖에는 TimerTrigger(시뮬레이터). 결정적·외부의존 없음.
    - picam 모드: AIVIS_TRIGGER=gpio 면 근접센서, 아니면 SW 타이머.
    - genicam 모드: AIVIS_TRIGGER 로 실 트리거 선택(dio/mqtt/gpio). 생성은 항상
      성공하고, wait_for_trigger 시점에 드라이버/SDK 를 요구한다(안내 예외).
    """
    mode = get_camera_mode()
    tmode = get_trigger_mode()
    if tmode == "vision":
        if camera is None:
            raise ValueError("AIVIS_TRIGGER=vision 에는 camera 가 필요하다(create_trigger(camera=...))")
        # roi: 검사 영역(화면 비율). 워커가 검사·사진 표시와 같은 값을 넘긴다.
        return VisionTrigger(camera, mode_provider=mode_provider, roi=roi)
    if mode == "sim":
        return TimerTrigger(interval_s=interval_s)
    if mode == "picam":
        # 라즈베리파이: AIVIS_TRIGGER=gpio 면 컨베이어 근접센서(GPIO)로 찍는다.
        # 기본은 종전대로 SW 타이머(센서 결선 전에도 돌아가게).
        if tmode == "gpio":
            return GpioTrigger()
        return TimerTrigger(interval_s=interval_s)
    # genicam 모드: 실 트리거 결선(생성은 성공, 대기 시 SDK/드라이버 필요).
    if tmode == "dio":
        return DigitalIOTrigger()
    if tmode == "mqtt":
        return MqttTrigger()
    if tmode == "gpio":
        return GpioTrigger()
    # timer/filewatch 등은 시뮬 트리거로 폴백(개발/계측용).
    return TimerTrigger(interval_s=interval_s)
