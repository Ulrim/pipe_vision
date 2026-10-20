"""라이브 뷰 송출 — 워커 쪽 (2026-10-20, services/api/core/live.py 짝).

설치할 때 카메라 위치·초점·노출·검사 영역을 맞추려면 지금 카메라가 보는 장면을 계속 봐야
한다. 화면이 라이브 뷰를 켜면 하트비트 응답에 `live: true` 가 오고, 이 송출기가 저해상도 흑백
미리보기(파이 lores 스트림)를 초당 몇 장 JPEG 으로 올린다. 응답이 `live: false` 가 되면 멈춘다
— 아무도 안 볼 때는 영상이 회선을 쓰지 않는다.

장면마다 **초점 지수**(검사 영역의 라플라시안 분산 — 상용 카메라 설치 도구의 포커스 값과 같은
방식)와 밝기·포화·암부 비율을 함께 보낸다. 렌즈를 돌리며 초점 지수가 최고가 되는 자리를 찾고,
포화가 보이면 노출·조명을 줄인다.

카메라는 검사 루프·영상 트리거·이 송출기가 함께 쓴다. LockedCamera 가 한 번에 하나만 쓰게 한다.
"""
from __future__ import annotations

import base64
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, Optional

import cv2
import numpy as np

log = logging.getLogger("aivis.vision.worker.live")


def _envf(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


class LockedCamera:
    """카메라 접근을 한 번에 하나로 묶는다(검사 촬영 · 영상 트리거 · 라이브 뷰).

    picamera2 는 여러 스레드가 동시에 capture 하면 안전하지 않다. 그 밖의 속성은 그대로 넘긴다.
    """

    def __init__(self, camera) -> None:
        self._cam = camera
        self._lock = threading.RLock()

    @property
    def inner(self):
        return self._cam

    def grab(self):
        with self._lock:
            return self._cam.grab()

    def preview(self, width: int = 320):
        with self._lock:
            return self._cam.preview(width)

    def configure(self, recipe):
        with self._lock:
            return self._cam.configure(recipe)

    def close(self):
        with self._lock:
            return self._cam.close()

    def __getattr__(self, name):
        return getattr(self._cam, name)


def frame_metrics(gray: np.ndarray, roi: Optional[tuple] = None) -> Dict[str, float]:
    """설치 조정용 지표 — 검사 영역 안에서 잰다(영역 밖 배경이 초점 값을 흐리지 않게).

    focus: 라플라시안 분산(클수록 선명). 절대값은 장면마다 달라 '최고값을 찾는' 데 쓴다.
    brightness: 평균 밝기 0~255. saturated_pct / dark_pct: ≥250 / ≤5 화소 비율(%).
    """
    g = gray if gray.ndim == 2 else cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
    h, w = g.shape[:2]
    if roi:
        x0, y0, x1, y1 = roi
        xa, xb = int(x0 * w), max(int(x0 * w) + 2, int(round(x1 * w)))
        ya, yb = int(y0 * h), max(int(y0 * h) + 2, int(round(y1 * h)))
        g = g[ya:yb, xa:xb]
    if g.size == 0:
        return {"focus": 0.0, "brightness": 0.0, "saturated_pct": 0.0, "dark_pct": 0.0}
    return {
        "focus": round(float(cv2.Laplacian(g, cv2.CV_64F).var()), 2),
        "brightness": round(float(g.mean()), 1),
        "saturated_pct": round(float((g >= 250).mean() * 100.0), 2),
        "dark_pct": round(float((g <= 5).mean() * 100.0), 2),
    }


class LivePublisher:
    """화면이 볼 때만 미리보기를 올리는 송출기(데몬 스레드 1개, 켜질 때 시작).

    환경변수(기본값):
      AIVIS_LIVE_FPS            2     초당 장수
      AIVIS_LIVE_WIDTH          640   보낼 가로 화소(카메라 lores 폭보다 크게 잡아도 늘리지 않는다)
      AIVIS_LIVE_JPEG_QUALITY   70
    """

    _MAX_FAILS = 10  # 연속 전송 실패가 이만큼이면 멈춘다(다음 하트비트가 다시 켠다)

    def __init__(
        self,
        camera_getter: Callable[[], Any],
        client,
        cam_id: str,
        *,
        roi: Optional[tuple] = None,
        on_capture: Optional[Callable[[], None]] = None,
        trigger_getter: Optional[Callable[[], Any]] = None,
        fps: Optional[float] = None,
        width: Optional[int] = None,
        quality: Optional[int] = None,
        sleep=time.sleep,
    ) -> None:
        self._camera = camera_getter
        self._client = client
        self.cam_id = cam_id
        self.roi = tuple(roi) if roi else None
        self._on_capture = on_capture
        self._trigger = trigger_getter
        self.fps = max(0.2, float(fps if fps is not None else _envf("AIVIS_LIVE_FPS", 2)))
        self.width = int(width if width is not None else _envf("AIVIS_LIVE_WIDTH", 640))
        self.quality = int(quality if quality is not None else _envf("AIVIS_LIVE_JPEG_QUALITY", 70))
        self._sleep = sleep
        self._active = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._fails = 0
        self._last_err = -1e9
        self.sent = 0

    @property
    def active(self) -> bool:
        return self._active.is_set()

    def activate(self) -> None:
        if not self._active.is_set():
            log.info("라이브 뷰 시작(%s, %.1f장/초, 폭 %dpx)", self.cam_id, self.fps, self.width)
            self._fails = 0
        self._active.set()
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, name="aivis-live", daemon=True)
            self._thread.start()

    def deactivate(self) -> None:
        if self._active.is_set():
            log.info("라이브 뷰 정지(%s, %d장 보냄)", self.cam_id, self.sent)
        self._active.clear()

    def stop(self) -> None:
        self._stop.set()
        self._active.clear()

    def _loop(self) -> None:
        period = 1.0 / self.fps
        while not self._stop.is_set():
            if not self._active.wait(timeout=1.0):
                continue
            t0 = time.monotonic()
            self.publish_once()
            wait = period - (time.monotonic() - t0)
            if wait > 0:
                self._sleep(wait)

    def publish_once(self) -> Optional[dict]:
        """한 장 찍어 올린다. 응답(live/capture)을 반영하고 돌려준다. 예외를 던지지 않는다."""
        try:
            cam = self._camera()
            if cam is None:
                return None
            gray = np.asarray(cam.preview(self.width))
            if gray.ndim == 3:
                gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
            metrics = frame_metrics(gray, self.roi)
            ok, buf = cv2.imencode(".jpg", gray, [int(cv2.IMWRITE_JPEG_QUALITY), self.quality])
            if not ok:
                raise RuntimeError("JPEG 인코딩 실패")
            payload: Dict[str, Any] = {
                "jpeg_b64": base64.b64encode(buf.tobytes()).decode("ascii"),
                "width": int(gray.shape[1]),
                "height": int(gray.shape[0]),
                "roi": list(self.roi) if self.roi else None,
                **metrics,
            }
            trig = self._trigger() if self._trigger else None
            tm = getattr(trig, "last_metrics", None)
            if isinstance(tm, dict) and tm:
                payload["trigger"] = {k: tm.get(k) for k in ("mode", "motion", "change") if k in tm}
            resp = self._client.put_live_frame(self.cam_id, payload)
        except Exception as exc:  # noqa: BLE001 - 라이브 뷰는 검사를 막지 않는다
            resp = None
            now = time.monotonic()
            if now - self._last_err > 60:
                log.warning("라이브 뷰 프레임 실패(계속): %s", exc)
                self._last_err = now
        if resp is None:
            self._fails += 1
            if self._fails >= self._MAX_FAILS:
                log.warning("라이브 뷰 전송이 %d번 연속 실패 — 멈춤", self._fails)
                self.deactivate()
            return None
        self._fails = 0
        self.sent += 1
        if resp.get("capture") and self._on_capture:
            self._on_capture()
        if not resp.get("live"):
            self.deactivate()
        return resp
