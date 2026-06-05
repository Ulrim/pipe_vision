"""P7 실카메라 통합 준비 테스트 — GenICam 어댑터/트리거/팩토리.

원칙(§6.1): SDK 미설치 환경에서도 import/생성은 성공하고, 디바이스 접근
시점(grab/configure/wait)에 안내 예외가 발생해야 한다. 모든 테스트는 실
SDK 없이 통과한다(모킹/동적import 가드).
"""
from __future__ import annotations

import numpy as np
import pytest

from vision.acquisition import (
    CameraError,
    DigitalIOTrigger,
    GenICamCamera,
    GenICamSDKError,
    MqttTrigger,
    SimulatorCamera,
    TriggerSDKError,
    create_camera,
    create_trigger,
    extract_strobe_config,
    get_trigger_mode,
    map_recipe_to_genicam,
)


# --- 생성은 항상 성공(SDK 미설치여도) ---
def test_genicam_construct_succeeds_without_sdk():
    cam = GenICamCamera()  # import/생성은 SDK 없이도 성공해야 한다.
    assert isinstance(cam, GenICamCamera)
    cam.close()  # 미연결 close 는 무해.


def test_factory_genicam_branch(monkeypatch, dataset_dir):
    monkeypatch.setenv("AIVIS_CAMERA", "genicam")
    cam = create_camera()
    assert isinstance(cam, GenICamCamera)


def test_factory_sim_still_works(monkeypatch, dataset_dir):
    monkeypatch.setenv("AIVIS_CAMERA", "sim")
    cam = create_camera(dataset_dir=str(dataset_dir), view_filter="SIDE")
    assert isinstance(cam, SimulatorCamera)
    assert cam.grab().ndim == 3


# --- grab/configure 는 SDK 미구성 시 안내 예외 ---
def test_genicam_grab_raises_guided_error_without_sdk():
    cam = GenICamCamera(backend="harvesters", cti_path=None)
    with pytest.raises(GenICamSDKError) as ei:
        cam.grab()
    # 메시지에 필요한 SDK/환경변수 안내가 담겨야 한다.
    assert "harvesters" in str(ei.value)


def test_genicam_grab_is_also_camera_error_and_notimplemented():
    """grab 예외는 CameraError(취득실패) 이며 NotImplementedError(스텁) 호환."""
    cam = GenICamCamera()
    with pytest.raises(CameraError):
        cam.grab()
    with pytest.raises(NotImplementedError):
        cam.grab()


def test_genicam_configure_maps_then_requires_sdk():
    cam = GenICamCamera(backend="harvesters", cti_path=None)
    recipe = {"exposure_us": 5000, "gain_db": 2.0, "pixel_format": "BGR8"}
    with pytest.raises(GenICamSDKError):
        cam.configure(recipe)
    # 매핑 자체는 SDK 없이 수행되어 node_values 에 남는다.
    assert cam.node_values["ExposureTime"] == 5000
    assert cam.node_values["Gain"] == 2.0
    assert cam.node_values["PixelFormat"] == "BGR8"


def test_pypylon_backend_guided_error():
    cam = GenICamCamera(backend="pypylon")
    with pytest.raises(GenICamSDKError) as ei:
        cam.grab()
    assert "pypylon" in str(ei.value)


def test_unknown_backend_error():
    cam = GenICamCamera(backend="nope")
    with pytest.raises(GenICamSDKError):
        cam.grab()


# --- recipe → SFNC 노드 매핑 단위검증 ---
def test_map_recipe_to_genicam_full():
    recipe = {
        "exposure_us": 3000,
        "exposure_auto": "Off",
        "gain_db": 1.5,
        "pixel_format": "BayerRG8",
        "trigger_mode": "On",
        "trigger_source": "Line0",
        "width": 1024,
        "height": 768,
        "offset_x": 8,
        "offset_y": 4,
        "black_level": 0.1,
        "gamma": 1.0,
    }
    nodes = map_recipe_to_genicam(recipe)
    assert nodes["ExposureTime"] == 3000
    assert nodes["ExposureAuto"] == "Off"
    assert nodes["Gain"] == 1.5
    assert nodes["PixelFormat"] == "BayerRG8"
    assert nodes["TriggerMode"] == "On"
    assert nodes["TriggerSource"] == "Line0"
    assert nodes["Width"] == 1024 and nodes["Height"] == 768
    assert nodes["OffsetX"] == 8 and nodes["OffsetY"] == 4
    assert nodes["BlackLevel"] == 0.1 and nodes["Gamma"] == 1.0


def test_map_recipe_ignores_lighting_and_unknown():
    recipe = {"exposure_us": 100, "lighting": "dome", "foo": "bar"}
    nodes = map_recipe_to_genicam(recipe)
    assert "ExposureTime" in nodes
    # 조명/미지 키는 노드맵에서 제외.
    assert "lighting" not in nodes and "foo" not in nodes


def test_map_recipe_raw_passthrough():
    nodes = map_recipe_to_genicam({"GenICam.VendorNode": 7})
    assert nodes["VendorNode"] == 7


def test_extract_strobe_config():
    recipe = {"exposure_us": 1, "lighting": "bar", "light_intensity": 80}
    strobe = extract_strobe_config(recipe)
    assert strobe == {"lighting": "bar", "light_intensity": 80}


def test_map_recipe_empty():
    assert map_recipe_to_genicam(None) == {}
    assert map_recipe_to_genicam({}) == {}


# --- 픽셀포맷 → BGR 변환 골격 ---
def test_to_bgr_mono_and_rgb():
    cam = GenICamCamera()
    mono = np.zeros((4, 4), dtype=np.uint8)
    bgr = cam._to_bgr(mono, "Mono8")
    assert bgr.shape == (4, 4, 3)
    rgb = np.dstack([
        np.full((2, 2), 10, np.uint8),
        np.full((2, 2), 20, np.uint8),
        np.full((2, 2), 30, np.uint8),
    ])
    out = cam._to_bgr(rgb, "RGB8")
    # RGB→BGR 채널 스왑.
    assert out[0, 0, 0] == 30 and out[0, 0, 2] == 10


def test_to_bgr_passthrough_bgr():
    cam = GenICamCamera()
    bgr = np.zeros((3, 3, 3), dtype=np.uint8)
    assert cam._to_bgr(bgr, "BGR8") is bgr


# --- 실 트리거: 생성 성공, 대기 시 안내 예외 ---
def test_dio_trigger_construct_and_guided_error():
    trig = DigitalIOTrigger(channel=1)
    assert trig.channel == 1
    with pytest.raises(TriggerSDKError):
        trig.wait_for_trigger(timeout=0.0)


def test_mqtt_trigger_construct_without_paho(monkeypatch):
    # paho 미설치 가정: import 가드가 안내 예외로 떨어져야 한다.
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name.startswith("paho"):
            raise ImportError("no paho")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    trig = MqttTrigger(topic="aivis/trigger")  # 생성은 성공.
    with pytest.raises(TriggerSDKError) as ei:
        trig.wait_for_trigger(timeout=0.0)
    assert "paho" in str(ei.value)


def test_factory_trigger_genicam_dio(monkeypatch):
    monkeypatch.setenv("AIVIS_CAMERA", "genicam")
    monkeypatch.setenv("AIVIS_TRIGGER", "dio")
    trig = create_trigger()
    assert isinstance(trig, DigitalIOTrigger)


def test_factory_trigger_genicam_mqtt(monkeypatch):
    monkeypatch.setenv("AIVIS_CAMERA", "genicam")
    monkeypatch.setenv("AIVIS_TRIGGER", "mqtt")
    trig = create_trigger()
    assert isinstance(trig, MqttTrigger)


def test_factory_trigger_sim_is_timer(monkeypatch):
    monkeypatch.setenv("AIVIS_CAMERA", "sim")
    monkeypatch.delenv("AIVIS_TRIGGER", raising=False)
    from vision.acquisition import TimerTrigger

    assert isinstance(create_trigger(), TimerTrigger)
    assert get_trigger_mode() in ("timer", "dio", "mqtt", "filewatch")
