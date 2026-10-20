"""라이브 뷰·1장 촬영 — 워커 쪽 (2026-10-20).

1. 초점 지수: 선명한 장면 > 흐린 장면, 검사 영역 안에서만 잰다. 포화·암부 비율.
2. 송출기: 응답이 live 인 동안 보내고, false 면 멈추고, capture 면 1장 촬영을 알린다.
   연속 실패가 쌓이면 멈춘다(다음 하트비트가 다시 켠다).
3. 워커: 하트비트 응답 live → 송출 시작, capture → 1장 촬영. 1장 촬영은 영상 트리거의
   '빈 장면이면 저장 안 함' 을 건너뛴다(사람이 누른 사진은 남긴다).
4. 카메라 잠금 프록시는 다른 속성을 그대로 넘긴다.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import sys
import time
from pathlib import Path

import cv2
import httpx
import numpy as np

_SERVICES_DIR = Path(__file__).resolve().parents[2]
if str(_SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_DIR))

from vision.acquisition import AcquisitionService, VisionTrigger  # noqa: E402
from vision.acquisition.camera import _to_preview  # noqa: E402
from vision.worker.live import LivePublisher, LockedCamera, frame_metrics  # noqa: E402
from vision.worker.runner import Worker  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "_test_worker_stubs_live", Path(__file__).with_name("test_worker.py"))
_tw = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_tw)


class LiveBackend(_tw.FakeBackend):
    """하트비트 응답에 화면 요청을 싣고, 라이브 프레임을 받는다."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.status_reply = {"status": "ok"}
        self.frame_reply = {"live": True, "capture": False}
        self.frames: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/inspection/status" and request.method == "POST":
            self.statuses.append(json.loads(request.content))
            reply, self.status_reply = self.status_reply, {"status": "ok"}
            return httpx.Response(202, json=reply)
        if path.startswith("/system/live/") and path.endswith("/frame") and request.method == "PUT":
            self.frames.append(json.loads(request.content))
            return httpx.Response(200, json=self.frame_reply)
        return super().handler(request)


def _client(backend):
    return _tw._client(backend)


class StillCam:
    def __init__(self, frame):
        self.frame = frame
        self.previews = 0

    def preview(self, width=320):
        self.previews += 1
        return _to_preview(self.frame, width)

    def grab(self):
        return self.frame.copy()

    def configure(self, recipe):
        pass

    def close(self):
        pass


def _checker(sharp=True, size=(240, 320)):
    """16px 바둑판(선명) 또는 그걸 흐린 것."""
    img = np.kron((np.indices((size[0] // 16, size[1] // 16)).sum(0) % 2) * 255,
                  np.ones((16, 16))).astype(np.uint8)
    return img if sharp else cv2.GaussianBlur(img, (0, 0), 4)


# --- 1. 지표 ------------------------------------------------------------------
def test_focus_metric_prefers_sharp_and_uses_roi():
    sharp, blur = frame_metrics(_checker(True)), frame_metrics(_checker(False))
    assert sharp["focus"] > 5 * blur["focus"], (sharp, blur)
    img = _checker(True)
    img[:, 160:] = 128  # 오른쪽 절반은 평평
    left = frame_metrics(img, (0.0, 0.0, 0.5, 1.0))["focus"]
    right = frame_metrics(img, (0.5, 0.0, 1.0, 1.0))["focus"]
    assert left > 100 * max(right, 1e-6), "검사 영역 안만 잰다"


def test_saturation_and_dark_percentages():
    img = np.full((100, 100), 128, np.uint8)
    img[:10] = 255
    img[90:] = 0
    m = frame_metrics(img)
    assert m["saturated_pct"] == 10.0 and m["dark_pct"] == 10.0
    assert 120 < m["brightness"] < 135


# --- 2. 송출기 ----------------------------------------------------------------
class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []

    def put_live_frame(self, cam_id, payload):
        self.sent.append((cam_id, payload))
        return self.replies.pop(0) if self.replies else {"live": True, "capture": False}


def test_publisher_payload_and_commands():
    cam = StillCam(cv2.cvtColor(_checker(True), cv2.COLOR_GRAY2BGR))
    caps = []
    client = FakeClient([{"live": True, "capture": True}, {"live": False, "capture": False}])
    pub = LivePublisher(lambda: cam, client, "PI-1", roi=(0.1, 0.1, 0.9, 0.9),
                        on_capture=lambda: caps.append(1), width=160)
    pub._active.set()
    r = pub.publish_once()
    assert r == {"live": True, "capture": True} and caps == [1] and pub.active
    cam_id, payload = client.sent[0]
    assert cam_id == "PI-1" and payload["width"] == 160 and payload["roi"] == [0.1, 0.1, 0.9, 0.9]
    jpeg = base64.b64decode(payload["jpeg_b64"])
    assert jpeg[:2] == b"\xff\xd8"
    assert cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE).shape == (120, 160)
    assert payload["focus"] > 0 and "saturated_pct" in payload
    pub.publish_once()
    assert not pub.active, "화면이 안 보면 멈춘다"


def test_publisher_stops_after_repeated_failures():
    cam = StillCam(np.zeros((60, 80, 3), np.uint8))

    class Down:
        def put_live_frame(self, *_a):
            return None

    pub = LivePublisher(lambda: cam, Down(), "PI-1", width=80)
    pub._active.set()
    for _ in range(LivePublisher._MAX_FAILS):
        pub.publish_once()
    assert not pub.active


def test_locked_camera_passthrough():
    cam = StillCam(np.zeros((60, 80, 3), np.uint8))
    cam.special = "x"
    lc = LockedCamera(cam)
    assert lc.special == "x" and lc.inner is cam
    assert lc.preview(40).shape == (30, 40) and lc.grab().shape == (60, 80, 3)


# --- 3. 워커 ------------------------------------------------------------------
def _worker(tmp_path, backend, frame, expected=1):
    w = Worker(_tw._cfg(tmp_path, max_iterations=1), client=_client(backend))
    assert w.startup() is True
    w.item = w.item.model_copy(update={"expected_count": expected})
    cam = StillCam(frame)
    w.camera = LockedCamera(cam)
    w.acq = AcquisitionService(camera=w.camera, max_retries=1, grab_timeout_s=5)
    return w, cam


def test_heartbeat_live_starts_streaming_frames(tmp_path):
    backend = LiveBackend(master_requires_auth=True)
    frame = cv2.cvtColor(_checker(True), cv2.COLOR_GRAY2BGR)
    w, _cam = _worker(tmp_path, backend, frame)
    backend.status_reply = {"status": "broadcast", "live": True, "capture": False}
    w._send_idle_status()
    assert w.live.active
    deadline = time.monotonic() + 3
    while not backend.frames and time.monotonic() < deadline:
        time.sleep(0.05)
    assert backend.frames, "라이브 뷰가 켜지면 프레임이 올라간다"
    backend.frame_reply = {"live": False, "capture": False}
    deadline = time.monotonic() + 3
    while w.live.active and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not w.live.active, "화면이 닫히면 멈춘다"
    w.shutdown()


def test_manual_capture_bypasses_vision_empty_gate(tmp_path):
    backend = LiveBackend(master_requires_auth=True)
    empty = np.full((240, 320, 3), 12, np.uint8)
    w, cam = _worker(tmp_path, backend, empty, expected=1)
    w.trigger = VisionTrigger(w.camera, fps=10, settle_ms=0, preview_width=160)

    w.run_once()  # 영상 트리거가 빈 장면에 쏜 경우 — 저장하지 않는다
    assert backend.posted == []

    backend.status_reply = {"status": "broadcast", "live": False, "capture": True}
    w._send_idle_status()
    assert w._capture.is_set(), "하트비트 응답의 1장 촬영 요청"
    w._capture.clear()
    w._run_manual_capture()
    assert len(backend.posted) == 1, "사람이 누른 1장 촬영은 제품이 안 보여도 결과를 남긴다"
    assert w._manual is False
    w.shutdown()


def test_run_loop_handles_capture_before_waiting(tmp_path, monkeypatch):
    backend = LiveBackend(master_requires_auth=True)
    w, _cam = _worker(tmp_path, backend, np.full((240, 320, 3), 12, np.uint8))
    calls = []
    monkeypatch.setattr(w, "_run_manual_capture", lambda: calls.append(1))
    monkeypatch.setattr(w, "startup", lambda: True)
    w._capture.set()
    w.run()
    assert calls == [1]
