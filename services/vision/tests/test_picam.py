"""Raspberry Pi Camera v3(IMX708) 어댑터/레시피/팩토리 테스트.

원칙(§6.1): picamera2/libcamera 미설치 환경(CI/dev)에서도 import/생성/close 는
성공하고, 디바이스 접근 시점(configure/grab)에 안내 예외(PiCameraSDKError)가
발생해야 한다. 모든 테스트는 실 picamera2 없이 통과한다.
"""
from __future__ import annotations

import numpy as np
import pytest

from vision.acquisition import (
    CameraError,
    PiCameraAdapter,
    PiCameraError,
    PiCameraSDKError,
    SimulatorCamera,
    create_camera,
    create_trigger,
    map_recipe_to_picamera,
)
from vision.acquisition.camera import (
    GenICamCamera,
    _finalize_pi_frame,
    _parse_size,
)


# --- 생성은 항상 성공(picamera2 미설치여도) ---
def test_picam_construct_succeeds_without_picamera2():
    cam = PiCameraAdapter()  # import/생성은 picamera2 없이도 성공해야 한다.
    assert isinstance(cam, PiCameraAdapter)
    cam.close()  # 미open close 는 무해.
    cam.close()  # 이중 close 도 무해.


def test_picam_env_defaults(monkeypatch):
    monkeypatch.delenv("AIVIS_PICAM_SIZE", raising=False)
    monkeypatch.delenv("AIVIS_PICAM_SWAP_RB", raising=False)
    monkeypatch.delenv("AIVIS_PICAM_WARMUP_FRAMES", raising=False)
    cam = PiCameraAdapter()
    assert cam._size == (2304, 1296)
    assert cam.swap_rb is False
    assert cam.warmup_frames == 2


def test_picam_env_overrides(monkeypatch):
    monkeypatch.setenv("AIVIS_PICAM_SIZE", "1920x1080")
    monkeypatch.setenv("AIVIS_PICAM_SWAP_RB", "true")
    monkeypatch.setenv("AIVIS_PICAM_WARMUP_FRAMES", "5")
    cam = PiCameraAdapter()
    assert cam._size == (1920, 1080)
    assert cam.swap_rb is True
    assert cam.warmup_frames == 5


def test_picam_ctor_args_override_env(monkeypatch):
    monkeypatch.setenv("AIVIS_PICAM_SIZE", "1920x1080")
    cam = PiCameraAdapter(size="640x480", swap_rb=True, warmup_frames=0)
    assert cam._size == (640, 480)
    assert cam.swap_rb is True
    assert cam.warmup_frames == 0


# --- 팩토리 ---
def test_factory_picam_branch(monkeypatch):
    monkeypatch.setenv("AIVIS_CAMERA", "picam")
    cam = create_camera()
    assert isinstance(cam, PiCameraAdapter)


def test_factory_sim_and_genicam_still_work(monkeypatch, dataset_dir):
    monkeypatch.setenv("AIVIS_CAMERA", "sim")
    cam = create_camera(dataset_dir=str(dataset_dir), view_filter="SIDE")
    assert isinstance(cam, SimulatorCamera)
    assert cam.grab().ndim == 3
    monkeypatch.setenv("AIVIS_CAMERA", "genicam")
    assert isinstance(create_camera(), GenICamCamera)


def test_factory_picam_trigger_is_timer(monkeypatch):
    monkeypatch.setenv("AIVIS_CAMERA", "picam")
    from vision.acquisition import TimerTrigger

    assert isinstance(create_trigger(), TimerTrigger)


# --- grab/configure 는 picamera2 미설치 시 안내 예외 ---
def test_picam_grab_raises_guided_error_without_picamera2():
    cam = PiCameraAdapter()
    with pytest.raises(PiCameraSDKError) as ei:
        cam.grab()
    msg = str(ei.value)
    assert "picamera2" in msg
    assert "apt install" in msg


def test_picam_sdk_error_is_camera_error_not_notimplemented():
    """PiCameraSDKError 는 CameraError 지만 NotImplementedError 는 아니다.

    (라즈베리파이에서는 실제 동작하는 어댑터이므로 스텁이 아니다.)
    """
    cam = PiCameraAdapter()
    with pytest.raises(CameraError):
        cam.grab()
    assert not isinstance(PiCameraSDKError(""), NotImplementedError)
    assert issubclass(PiCameraSDKError, PiCameraError)
    assert issubclass(PiCameraError, CameraError)


def test_picam_configure_maps_then_requires_picamera2():
    cam = PiCameraAdapter()
    recipe = {
        "exposure_us": 4000,
        "analogue_gain": 2.0,
        "af_mode": "manual",
        "lens_position": 1.5,
    }
    with pytest.raises(PiCameraSDKError):
        cam.configure(recipe)
    # 매핑 자체는 picamera2 없이 수행되어 controls 에 남는다.
    assert cam.controls["ExposureTime"] == 4000
    assert cam.controls["AnalogueGain"] == 2.0
    assert cam.controls["AfMode"] == "Manual"
    assert cam.controls["LensPosition"] == 1.5


# --- recipe → picamera2 컨트롤 매핑 순수 단위검증 ---
def test_map_recipe_exposure_gain_and_types():
    controls = map_recipe_to_picamera(
        {"exposure_us": 3000.0, "analogue_gain": 1}
    )
    assert controls["ExposureTime"] == 3000
    assert isinstance(controls["ExposureTime"], int)
    assert controls["AnalogueGain"] == 1.0
    assert isinstance(controls["AnalogueGain"], float)


def test_map_recipe_af_manual_lens_speed():
    controls = map_recipe_to_picamera(
        {
            "af_mode": "MANUAL",
            "lens_position": 2.0,
            "af_speed": "Fast",
        }
    )
    assert controls["AfMode"] == "Manual"     # 정규 문자열(enum 아님)
    assert controls["LensPosition"] == 2.0
    assert controls["AfSpeed"] == "Fast"


def test_map_recipe_af_continuous_and_auto():
    assert map_recipe_to_picamera({"af_mode": "continuous"})["AfMode"] == "Continuous"
    assert map_recipe_to_picamera({"af_mode": "auto"})["AfMode"] == "Auto"


def test_map_recipe_af_speed_normal():
    assert map_recipe_to_picamera({"af_speed": "normal"})["AfSpeed"] == "Normal"


def test_map_recipe_unknown_af_token_ignored():
    controls = map_recipe_to_picamera({"af_mode": "wobble", "af_speed": "turbo"})
    assert "AfMode" not in controls
    assert "AfSpeed" not in controls


def test_map_recipe_awb_and_passthrough():
    controls = map_recipe_to_picamera(
        {
            "awb_enable": False,
            "brightness": 0.1,
            "contrast": 1.2,
            "saturation": 0.9,
            "sharpness": 1.5,
        }
    )
    assert controls["AwbEnable"] is False
    assert controls["Brightness"] == 0.1
    assert controls["Contrast"] == 1.2
    assert controls["Saturation"] == 0.9
    assert controls["Sharpness"] == 1.5


def test_map_recipe_raw_picam_passthrough():
    controls = map_recipe_to_picamera({"PiCam.NoiseReductionMode": 2})
    assert controls["NoiseReductionMode"] == 2


def test_map_recipe_gain_db_conversion():
    # analogue_gain 없고 gain_db=6 → 10**(6/20) ≈ 1.9953 배수.
    controls = map_recipe_to_picamera({"gain_db": 6.0})
    assert controls["AnalogueGain"] == pytest.approx(10.0 ** (6.0 / 20.0))
    assert isinstance(controls["AnalogueGain"], float)
    # 0 dB → 1.0 배수.
    assert map_recipe_to_picamera({"gain_db": 0})["AnalogueGain"] == pytest.approx(1.0)


def test_map_recipe_analogue_gain_takes_priority_over_db():
    controls = map_recipe_to_picamera({"analogue_gain": 4.0, "gain_db": 6.0})
    assert controls["AnalogueGain"] == 4.0  # gain_db 무시.


def test_map_recipe_size_excluded():
    controls = map_recipe_to_picamera(
        {"width": 2304, "height": 1296, "exposure_us": 100}
    )
    assert "width" not in controls and "height" not in controls
    assert "Width" not in controls and "Height" not in controls
    assert controls["ExposureTime"] == 100


def test_map_recipe_unknown_key_ignored():
    controls = map_recipe_to_picamera({"foo": "bar", "exposure_us": 1})
    assert "foo" not in controls
    assert controls == {"ExposureTime": 1}


def test_map_recipe_empty():
    assert map_recipe_to_picamera(None) == {}
    assert map_recipe_to_picamera({}) == {}


# --- 프레임 마감 헬퍼(swap_rb) ---
def test_finalize_frame_no_swap_passthrough():
    arr = np.dstack([
        np.full((2, 2), 10, np.uint8),   # ch0
        np.full((2, 2), 20, np.uint8),   # ch1
        np.full((2, 2), 30, np.uint8),   # ch2
    ])
    out = _finalize_pi_frame(arr, swap_rb=False)
    # RGB888 = BGR 메모리 → 그대로.
    assert out.shape == (2, 2, 3)
    assert out[0, 0, 0] == 10 and out[0, 0, 2] == 30


def test_finalize_frame_swap_rb():
    arr = np.dstack([
        np.full((2, 2), 10, np.uint8),
        np.full((2, 2), 20, np.uint8),
        np.full((2, 2), 30, np.uint8),
    ])
    out = _finalize_pi_frame(arr, swap_rb=True)
    # RGB2BGR → ch0/ch2 스왑.
    assert out[0, 0, 0] == 30 and out[0, 0, 2] == 10


def test_finalize_frame_rgba_trimmed_to_3ch():
    arr = np.zeros((2, 2, 4), dtype=np.uint8)
    out = _finalize_pi_frame(arr, swap_rb=False)
    assert out.shape == (2, 2, 3)


def test_finalize_frame_dtype_coerced():
    arr = np.zeros((2, 2, 3), dtype=np.uint16)
    out = _finalize_pi_frame(arr, swap_rb=False)
    assert out.dtype == np.uint8


def test_finalize_frame_bad_shape_raises():
    with pytest.raises(PiCameraError):
        _finalize_pi_frame(np.zeros((2, 2), dtype=np.uint8), swap_rb=False)
    with pytest.raises(PiCameraError):
        _finalize_pi_frame(None, swap_rb=False)


# --- size 파서 ---
def test_parse_size_ok():
    assert _parse_size("2304x1296") == (2304, 1296)
    assert _parse_size("640X480") == (640, 480)


def test_parse_size_bad():
    with pytest.raises(PiCameraError):
        _parse_size("not-a-size")


def test_picam_bad_size_env(monkeypatch):
    monkeypatch.setenv("AIVIS_PICAM_SIZE", "oops")
    with pytest.raises(PiCameraError):
        PiCameraAdapter()
