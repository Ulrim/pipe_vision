"""트리거 소스 추상화 (HAL) — CLAUDE.md §6.1, M1.

검사 1회 = 트리거 1회. 실물은 디지털 IO / MQTT, 개발/테스트는 타이머/파일워처.
인터페이스는 동일하게 유지하여 파이프라인 코드를 바꾸지 않는다.
"""
from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Set


class TriggerSource(ABC):
    """트리거 소스 인터페이스. wait_for_trigger() 가 1회 검사 신호를 블로킹 대기."""

    #: 실제 사건(제품 도착)으로 울리는 트리거인가. True 면 timeout(False 반환) 때
    #: 워커가 **촬영하지 않는다**(빈 컨베이어를 찍어 '미검출' 을 쏟아내지 않게).
    #: 타이머는 False — 시간이 되면 무조건 찍는다(종전 동작).
    event_driven: bool = False

    @abstractmethod
    def wait_for_trigger(self, timeout: Optional[float] = None) -> bool:
        """트리거 도착까지 대기. 도착하면 True, timeout 이면 False."""

    def close(self) -> None:
        pass

    def __enter__(self) -> "TriggerSource":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


class TimerTrigger(TriggerSource):
    """고정 주기 타이머 트리거(시뮬레이터). interval_s 마다 트리거 발생.

    interval_s=0 이면 즉시 반환(배치 처리/테스트용 — 최고 속도).
    """

    def __init__(self, interval_s: float = 0.0) -> None:
        self.interval_s = max(0.0, interval_s)
        self._last = time.monotonic()

    def wait_for_trigger(self, timeout: Optional[float] = None) -> bool:
        if self.interval_s <= 0:
            return True
        now = time.monotonic()
        next_at = self._last + self.interval_s
        remaining = next_at - now
        if remaining > 0:
            if timeout is not None and timeout < remaining:
                time.sleep(max(0.0, timeout))
                return False
            time.sleep(remaining)
        self._last = time.monotonic()
        return True


class FileWatchTrigger(TriggerSource):
    """파일 워처 트리거(시뮬레이터). watch_dir 에 새 이미지 파일이 생기면 트리거.

    폴링 방식(외부 의존성 없음). 이미 존재하던 파일은 트리거하지 않는다.
    """

    _EXTS = (".jpg", ".jpeg", ".png", ".bmp")

    def __init__(self, watch_dir: str, poll_interval_s: float = 0.05) -> None:
        self.watch_dir = Path(watch_dir)
        self.poll_interval_s = poll_interval_s
        self._seen: Set[str] = set(self._list())

    def _list(self) -> Set[str]:
        if not self.watch_dir.exists():
            return set()
        return {
            str(p)
            for p in self.watch_dir.iterdir()
            if p.is_file() and p.suffix.lower() in self._EXTS
        }

    def wait_for_trigger(self, timeout: Optional[float] = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            current = self._list()
            new = current - self._seen
            if new:
                self._seen = current
                return True
            self._seen |= current
            if deadline is not None and time.monotonic() >= deadline:
                return False
            time.sleep(self.poll_interval_s)


class TriggerSDKError(NotImplementedError):
    """실물 트리거 SDK/드라이버(IO·MQTT) 미구성 시 발생.

    NotImplementedError 계열이라 기존 '스텁 미구현' 핸들링과 호환되며,
    메시지에 필요한 패키지/환경변수를 담아 통합 단계 안내를 제공한다.
    """


class DigitalIOTrigger(TriggerSource):
    """실물 디지털 IO 트리거 (P7). 산업용 PC DI 채널 결선.

    원칙(§6.1): 생성은 항상 성공한다. 실제 IO 드라이버는 환경마다 다르므로
    (Advantech/Contec/USB-DIO 등) 동적 import 로 감싼다. wait_for_trigger
    시점에 드라이버 미구성이면 안내 예외(TriggerSDKError).

    환경변수:
    - AIVIS_DIO_DRIVER : IO 드라이버 식별자(통합 시 결선).
    - AIVIS_DIO_CHANNEL: DI 채널 번호(미지정 시 channel 인자).

    통합 단계 작업 목록(TODO):
    1. _open_driver(): 벤더 IO SDK 동적 import + 디바이스/채널 오픈.
    2. wait_for_trigger(): 채널 상승 에지를 폴링/인터럽트 대기(타임아웃 적용).
    """

    event_driven = True

    def __init__(self, channel: Optional[int] = None) -> None:
        env_ch = os.environ.get("AIVIS_DIO_CHANNEL")
        self.channel = channel if channel is not None else (
            int(env_ch) if env_ch is not None else None
        )
        self.driver_name = os.environ.get("AIVIS_DIO_DRIVER")
        self._handle = None

    def _open_driver(self):  # pragma: no cover - 통합 단계
        raise TriggerSDKError(
            "DigitalIOTrigger: 디지털 IO 드라이버 미구성. 실카메라 통합 시 "
            "산업용 PC DI 드라이버(예: Advantech/Contec)를 결선하고 "
            "AIVIS_DIO_DRIVER/AIVIS_DIO_CHANNEL 을 지정하라. "
            "개발/테스트는 TimerTrigger/FileWatchTrigger 사용."
        )

    def wait_for_trigger(self, timeout: Optional[float] = None) -> bool:
        if self._handle is None:
            self._open_driver()  # 미구성이면 안내 예외.
        # TODO(P7): 채널 상승 에지 대기(타임아웃 적용) → True/False.
        raise TriggerSDKError("DigitalIOTrigger.wait_for_trigger: IO 결선 필요(P7).")


class MqttTrigger(TriggerSource):
    """MQTT 이벤트 트리거 (P7). 내부 이벤트 버스 토픽 구독.

    원칙(§6.1): 생성은 항상 성공한다. paho-mqtt 는 동적 import 로 감싸
    **미설치 환경에서도 import/생성 시 죽지 않는다**. connect()/wait 시점에
    미설치/미연결이면 안내 예외(TriggerSDKError).

    환경변수:
    - AIVIS_MQTT_HOST (기본 localhost), AIVIS_MQTT_PORT (기본 1883)
    - AIVIS_MQTT_TRIGGER_TOPIC (기본 인자 topic)

    통합 단계 작업 목록(TODO):
    1. connect(): paho.mqtt.client.Client() 생성 + connect(host,port) +
       subscribe(topic) + loop_start(). on_message 에서 _event 셋.
    2. wait_for_trigger(): _event 를 timeout 까지 대기(threading.Event).
    """

    event_driven = True

    def __init__(
        self,
        topic: Optional[str] = None,
        *,
        host: Optional[str] = None,
        port: Optional[int] = None,
    ) -> None:
        self.topic = topic or os.environ.get("AIVIS_MQTT_TRIGGER_TOPIC")
        self.host = host or os.environ.get("AIVIS_MQTT_HOST", "localhost")
        self.port = int(port if port is not None else os.environ.get("AIVIS_MQTT_PORT", 1883))
        self._client = None
        self._connected = False

    def _require_paho(self):
        """paho-mqtt 동적 import. 미설치면 안내 예외."""
        try:
            import paho.mqtt.client as mqtt  # type: ignore
        except ImportError as exc:
            raise TriggerSDKError(
                "MqttTrigger: paho-mqtt 미설치. 실 트리거 통합 시 "
                "`pip install paho-mqtt` 하라. 개발/테스트는 "
                "TimerTrigger/FileWatchTrigger 사용."
            ) from exc
        return mqtt

    def connect(self) -> None:  # pragma: no cover - 통합 단계
        """MQTT 브로커 연결 + 토픽 구독(통합 단계 결선)."""
        self._require_paho()  # 미설치면 여기서 안내 예외.
        if not self.topic:
            raise TriggerSDKError(
                "MqttTrigger: 구독 토픽 미지정(AIVIS_MQTT_TRIGGER_TOPIC)."
            )
        # TODO(P7): Client 생성/connect/subscribe/loop_start + on_message 핸들러.
        raise TriggerSDKError(
            f"MqttTrigger.connect: MQTT 결선 필요(P7) "
            f"(host={self.host}, port={self.port}, topic={self.topic})."
        )

    def wait_for_trigger(self, timeout: Optional[float] = None) -> bool:
        if not self._connected:
            self.connect()  # 미설치/미연결이면 안내 예외.
        # TODO(P7): threading.Event 를 timeout 까지 대기.
        raise TriggerSDKError("MqttTrigger.wait_for_trigger: MQTT 결선 필요(P7).")


class GpioTrigger(TriggerSource):
    """라즈베리파이 GPIO 근접센서 트리거 — 제품이 오면 찍는다(2026-10-10 점검 보완).

    종전에는 파이에서도 소프트웨어 타이머로만 찍었다(점검: "촬영 신호가 타이머뿐").
    컨베이어 근접센서(광전/유도형)를 GPIO 에 물려, **비활성 → 활성 에지** 마다
    한 번 찍는다. 제품이 센서 앞에 머물러 있으면 다시 비활성이 될 때까지 다음
    촬영을 하지 않는다(같은 제품을 여러 번 찍지 않게).

    환경변수:
      AIVIS_TRIGGER_GPIO        BCM 핀 번호(필수)
      AIVIS_TRIGGER_ACTIVE_LOW  true(기본) — NPN 센서(감지 시 GND). PNP 면 false
      AIVIS_TRIGGER_DEBOUNCE_MS 기본 30 — 접점 떨림 무시
      AIVIS_TRIGGER_SETTLE_MS   기본 150 — 감지 후 촬영까지 대기. 파이 카메라는
                                롤링 셔터라 **멈춘 뒤** 찍어야 길이가 안 틀어진다
                                (컨베이어 정지·진동이 가라앉는 시간에 맞춘다)

    생성은 늘 성공한다(§6.1). GPIO 를 실제로 여는 것은 첫 대기 때이고, 파이가
    아니거나 gpiozero 가 없으면 그때 안내 예외(TriggerSDKError).
    """

    event_driven = True

    def __init__(
        self,
        pin: Optional[int] = None,
        *,
        active_low: Optional[bool] = None,
        debounce_ms: Optional[int] = None,
        settle_ms: Optional[int] = None,
        backend=None,
    ) -> None:
        env_pin = os.environ.get("AIVIS_TRIGGER_GPIO")
        self.pin = pin if pin is not None else (int(env_pin) if env_pin else None)
        self.active_low = (
            active_low if active_low is not None
            else os.environ.get("AIVIS_TRIGGER_ACTIVE_LOW", "true").lower() in ("1", "true", "yes")
        )
        self.debounce_ms = int(
            debounce_ms if debounce_ms is not None else os.environ.get("AIVIS_TRIGGER_DEBOUNCE_MS", 30)
        )
        self.settle_ms = int(
            settle_ms if settle_ms is not None else os.environ.get("AIVIS_TRIGGER_SETTLE_MS", 150)
        )
        self._backend = backend
        self._ready = False
        #: 직전 촬영 뒤 센서가 다시 비활성이 됐나(같은 제품 중복 촬영 방지).
        self._armed = True
        self.fired = 0

    def _ensure(self) -> None:
        if self._ready:
            return
        if self.pin is None:
            raise TriggerSDKError(
                "GpioTrigger: AIVIS_TRIGGER_GPIO(BCM 핀 번호)가 없습니다. "
                "근접센서를 물린 핀을 적거나 AIVIS_TRIGGER=timer 로 두세요."
            )
        if self._backend is None:
            from vision.fieldio.gpio import GpioUnavailable, build_gpio_backend

            try:
                self._backend = build_gpio_backend()
            except GpioUnavailable as exc:
                raise TriggerSDKError(f"GpioTrigger: {exc}") from exc
        self._backend.setup_input(self.pin, active_low=self.active_low, bounce_ms=self.debounce_ms)
        self._armed = not self._backend.is_active(self.pin)
        self._ready = True

    def wait_for_trigger(self, timeout: Optional[float] = None) -> bool:
        self._ensure()
        deadline = None if timeout is None else time.monotonic() + timeout
        if not self._armed:
            # 직전 제품이 아직 센서 앞에 있다 — 빠질 때까지 기다린다.
            if not self._backend.wait_inactive(self.pin, timeout):
                return False
            self._armed = True
        remain = None if deadline is None else max(0.0, deadline - time.monotonic())
        if not self._backend.wait_active(self.pin, remain):
            return False
        self._armed = False
        self.fired += 1
        if self.settle_ms > 0:
            time.sleep(self.settle_ms / 1000.0)
        return True

    def close(self) -> None:
        if self._backend is not None:
            try:
                self._backend.close()
            except Exception:  # noqa: BLE001
                pass


def _parse_roi(raw: Optional[str]) -> tuple:
    """검사 영역 "x0,y0,x1,y1"(화면 비율 0~1). 잘못되면 화면 전체."""
    try:
        x0, y0, x1, y1 = (float(v) for v in str(raw).split(","))
    except (TypeError, ValueError):
        return (0.0, 0.0, 1.0, 1.0)
    x0, x1 = sorted((min(max(x0, 0.0), 1.0), min(max(x1, 0.0), 1.0)))
    y0, y1 = sorted((min(max(y0, 0.0), 1.0), min(max(y1, 0.0), 1.0)))
    if x1 - x0 < 0.02 or y1 - y0 < 0.02:
        return (0.0, 0.0, 1.0, 1.0)
    return (x0, y0, x1, y1)


def _envf(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


class VisionTrigger(TriggerSource):
    """영상 트리거 — 센서 없이 카메라 영상으로 '찍을 때' 를 정한다(2026-10-16).

    카메라의 저해상도 흑백 미리보기(preview)를 초당 몇 장씩 **검사 영역만** 보고,
    고해상도 사진은 필요할 때 한 장만 찍는다. 영상은 파이 밖으로 나가지 않는다.

    **도착(arrival) — 길이·표면 스테이션**: 영역에서 큰 움직임이 있었고(다발이 들어옴)
    움직임이 멈춘 뒤 settle 동안 조용하면 한 번 쏜다. 같은 품목 다발은 서로 거의 똑같아
    보이므로 "지난 장면과 다른가" 로는 새 다발을 구분할 수 없다 — 대신 **움직이는 동안
    장면이 크게 바뀌었는가**(컨베이어가 움직였다)를 본다. 손이 잠깐 지나간 정도(작은
    변화)면 끝난 장면이 직전과 같을 때 다시 찍지 않는다. 파이 카메라는 롤링 셔터라
    멈춘 뒤 찍어야 길이가 안 틀어진다 — 근접센서는 '왔다' 만 알지만 이것은 멈춘 것을 본다.

    **반출(departure) — 크레이트 개수 스테이션**: 크레이트 한 판 = 개수 1건이어야 한다
    (MES 생산실적이 판 개수를 더한다). 작업자가 튜브를 넣을 때마다 세면 한 판이 여러 번
    더해진다. 그래서 조용해질 때마다 고해상도 사진을 **보관만** 하고, 크레이트가 빠지면
    (장면이 크게 바뀌거나 단면 무늬가 줄면) **빠지기 직전의 마지막 사진**을 한 번 낸다.
    워커는 take_frame() 으로 그 사진을 받는다.

    **빈 장면 학습**: 처음 조용해진 장면은 일단 쏜다. 워커가 그 사진에서 제품을 못 찾으면
    learn_empty() 를 불러 '빈 장면' 으로 기억하고, 이후 그 장면에서는 쏘지 않는다. 조명이
    바뀌어 빈 장면이 달라 보여도 한 번 더 쏘고 다시 배운다(최근 5개 기억).

    환경변수(기본값):
      AIVIS_VISION_ROI           "0,0,1,1"  검사 영역(화면 비율 x0,y0,x1,y1)
      AIVIS_VISION_FPS           8          미리보기 확인 빈도
      AIVIS_VISION_SETTLE_MS     400        도착: 멈춘 뒤 이만큼 조용하면 촬영
      AIVIS_VISION_DEPART_SETTLE_MS 1500    반출: 손이 빠지고 이만큼 조용하면 보관
      AIVIS_VISION_PIXEL_DELTA   20         밝기 차가 이보다 큰 화소를 '바뀐 화소'로
      AIVIS_VISION_MOTION        0.004      움직임 판정(바뀐 화소 비율, 연속 두 장)
      AIVIS_VISION_CHANGE        0.02       같은 장면 판정(바뀐 화소 비율)
      AIVIS_VISION_BIG_CHANGE    0.35       큰 변화(새 다발·크레이트 반출)
      AIVIS_VISION_DEPART_DROP   0.15       반출: 단면 무늬가 이 비율 이상 줄면 빠진 것
      AIVIS_VISION_MODE          auto       auto(검사 모드로 자동)|arrival|departure
      AIVIS_VISION_PREVIEW_WIDTH 320
    현장 조정: python -m vision.tools.vision_trigger_preview (docs/OPERATIONS_PI.md §11-1)
    """

    event_driven = True
    ARRIVAL = "arrival"
    DEPARTURE = "departure"
    _MAX_EMPTIES = 5

    def __init__(
        self,
        camera,
        *,
        mode_provider=None,
        roi: Optional[tuple] = None,
        fps: Optional[float] = None,
        settle_ms: Optional[float] = None,
        depart_settle_ms: Optional[float] = None,
        pixel_delta: Optional[int] = None,
        motion: Optional[float] = None,
        change: Optional[float] = None,
        big_change: Optional[float] = None,
        depart_drop: Optional[float] = None,
        mode: Optional[str] = None,
        preview_width: Optional[int] = None,
        clock=time.monotonic,
        sleep=time.sleep,
    ) -> None:
        import logging

        self._log = logging.getLogger("aivis.vision_trigger")
        self.camera = camera
        self.mode_provider = mode_provider
        forced = (mode or os.environ.get("AIVIS_VISION_MODE", "auto")).strip().lower()
        self.forced_mode = forced if forced in (self.ARRIVAL, self.DEPARTURE) else None
        self.roi = roi or _parse_roi(os.environ.get("AIVIS_VISION_ROI", "0,0,1,1"))
        self.fps = float(fps if fps is not None else _envf("AIVIS_VISION_FPS", 8))
        self.settle_s = (settle_ms if settle_ms is not None
                         else _envf("AIVIS_VISION_SETTLE_MS", 400)) / 1000.0
        self.depart_settle_s = (depart_settle_ms if depart_settle_ms is not None
                                else _envf("AIVIS_VISION_DEPART_SETTLE_MS", 1500)) / 1000.0
        self.pixel_delta = int(pixel_delta if pixel_delta is not None
                               else _envf("AIVIS_VISION_PIXEL_DELTA", 20))
        self.motion_thr = float(motion if motion is not None else _envf("AIVIS_VISION_MOTION", 0.004))
        self.change_thr = float(change if change is not None else _envf("AIVIS_VISION_CHANGE", 0.02))
        self.big_change = float(big_change if big_change is not None
                                else _envf("AIVIS_VISION_BIG_CHANGE", 0.35))
        self.depart_drop = float(depart_drop if depart_drop is not None
                                 else _envf("AIVIS_VISION_DEPART_DROP", 0.15))
        self.preview_width = int(preview_width if preview_width is not None
                                 else _envf("AIVIS_VISION_PREVIEW_WIDTH", 320))
        self._clock = clock
        self._sleep = sleep
        self.fired = 0
        self.last_metrics: dict = {}
        self._last_err_log = -1e9
        self._mode_seen: Optional[str] = None
        self._reset_state()

    # --- 상태 ---------------------------------------------------------------
    def _reset_state(self) -> None:
        self._prev = None            # 직전 미리보기(정규화 int16)
        self._still_since = None
        self._moved = True           # 기동 직후 첫 정지 장면은 한 번 본다
        self._episode_peak = 0.0     # 이번 움직임 동안 기준 장면 대비 최대 변화
        self._baseline = None        # 도착: 마지막으로 처리한 장면
        self._last_fired = None      # 마지막으로 낸 장면(빈 장면 학습용)
        self._held = None            # 반출: 보관 중인 마지막 정지 장면
        self._pending = None         # 반출: 워커에 넘길 사진
        self._empties: list = []

    def mode(self) -> str:
        if self.forced_mode:
            m = self.forced_mode
        else:
            m = None
            if self.mode_provider is not None:
                try:
                    m = self.mode_provider()
                except Exception:  # noqa: BLE001
                    m = None
            m = m if m in (self.ARRIVAL, self.DEPARTURE) else self.ARRIVAL
        if m != self._mode_seen:
            if self._mode_seen is not None:
                self._log.info("영상 트리거 모드 전환: %s → %s", self._mode_seen, m)
                empties = self._empties
                self._reset_state()
                self._empties = empties  # 같은 카메라의 빈 장면은 그대로 쓴다
            self._mode_seen = m
        return m

    # --- 측정 ---------------------------------------------------------------
    def _read(self):
        import cv2
        import numpy as np

        g = self.camera.preview(self.preview_width)
        g = np.asarray(g)
        if g.ndim == 3:
            g = cv2.cvtColor(g, cv2.COLOR_BGR2GRAY)
        h, w = g.shape[:2]
        x0, y0, x1, y1 = self.roi
        xa, xb = int(x0 * w), max(int(x0 * w) + 2, int(round(x1 * w)))
        ya, yb = int(y0 * h), max(int(y0 * h) + 2, int(round(y1 * h)))
        crop = cv2.GaussianBlur(np.ascontiguousarray(g[ya:yb, xa:xb]), (5, 5), 0)
        return crop, crop.astype(np.int16)

    def _frac(self, a, b) -> float:
        """두 장면에서 '바뀐 화소' 비율.

        화면 전체 밝기 변화(조명·자동노출)는 장면 변화가 아니다 — 차이 영상의 중앙값을
        빼고 센다. 각 장면의 중앙값을 따로 빼면 안 된다: 튜브가 영역의 절반쯤을 덮으면
        손 하나만 들어와도 중앙값이 배경↔튜브 밝기 사이를 오가 화면 전체가 바뀐 것처럼
        보인다(시험에서 확인). 차이의 중앙값은 국소 변화로는 움직이지 않는다.
        """
        import numpy as np

        if a is None or b is None or a.shape != b.shape:
            return 1.0
        d = a - b
        d = d - int(np.median(d))
        return float(np.mean(np.abs(d) > self.pixel_delta))

    @staticmethod
    def _texture(gray) -> float:
        """단면 무늬(튜브 끝 고리) 양 — 크레이트가 빠지면 준다."""
        import cv2
        import numpy as np

        return float(np.mean(np.abs(cv2.Laplacian(gray, cv2.CV_16S, ksize=3))))

    def _matches_empty(self, cur) -> bool:
        return any(self._frac(cur, e) <= self.change_thr for e in self._empties)

    # --- 한 장 처리 -----------------------------------------------------------
    def _step(self, gray, cur, now: float) -> bool:
        mode = self.mode()
        prev, self._prev = self._prev, cur
        if prev is None or prev.shape != cur.shape:
            self._still_since = None
            return False
        motion = self._frac(cur, prev)
        ref = self._baseline if mode == self.ARRIVAL else (
            self._held["preview"] if self._held else None)
        change = self._frac(cur, ref) if ref is not None else None
        self.last_metrics = {"mode": mode, "motion": round(motion, 4),
                             "change": None if change is None else round(change, 4)}
        if motion > self.motion_thr:
            self._still_since = None
            self._moved = True
            if change is not None:
                self._episode_peak = max(self._episode_peak, change)
            return False
        if self._still_since is None:
            self._still_since = now
            return False
        settle = self.settle_s if mode == self.ARRIVAL else self.depart_settle_s
        if now - self._still_since < settle or not self._moved:
            return False
        self._moved = False
        peak, self._episode_peak = self._episode_peak, 0.0
        if mode == self.ARRIVAL:
            return self._stable_arrival(cur, peak)
        return self._stable_departure(gray, cur)

    def _stable_arrival(self, cur, peak: float) -> bool:
        if self._matches_empty(cur):
            self._baseline = cur
            return False
        if (self._baseline is not None and peak < self.big_change
                and self._frac(cur, self._baseline) <= self.change_thr):
            return False  # 손이 잠깐 지나갔을 뿐 — 같은 다발이다
        self._baseline = cur
        self._last_fired = cur
        self.fired += 1
        return True

    def _stable_departure(self, gray, cur) -> bool:
        if self._matches_empty(cur):
            if self._held is None:
                return False
            return self._emit(None)  # 크레이트가 빠지고 빈 자리가 됐다
        tex = self._texture(gray)
        if self._held is None:
            self._hold(cur, tex)
            return False
        d = self._frac(cur, self._held["preview"])
        if d <= self.change_thr:
            return False  # 손이 지나갔을 뿐
        if d >= self.big_change or tex < self._held["texture"] * (1.0 - self.depart_drop):
            return self._emit((cur, tex))  # 크레이트가 빠졌다(또는 다른 판으로 바뀌었다)
        self._hold(cur, tex)  # 튜브를 더 넣었다 — 더 최신 장면을 보관
        return False

    def _hold(self, cur, tex: float) -> None:
        from datetime import datetime, timezone

        try:
            frame = self.camera.grab()
        except Exception as exc:  # noqa: BLE001
            self._log.warning("영상 트리거: 보관 사진 촬영 실패(%s)", exc)
            return
        self._held = {"frame": frame, "preview": cur, "texture": tex,
                      "at": datetime.now(timezone.utc)}

    def _emit(self, next_scene) -> bool:
        self._pending = self._held
        self._last_fired = self._held["preview"]
        self._held = None
        if next_scene is not None:
            self._hold(*next_scene)
        self.fired += 1
        return True

    # --- 공개 ---------------------------------------------------------------
    def poll_once(self) -> bool:
        """미리보기 한 장을 보고 판단한다(현장 조정 도구·시험용). 쏘면 True."""
        now = self._clock()
        gray, cur = self._read()
        return self._step(gray, cur, now)

    def wait_for_trigger(self, timeout: Optional[float] = None) -> bool:
        deadline = None if timeout is None else self._clock() + timeout
        period = 1.0 / self.fps if self.fps > 0 else 0.0
        while True:
            t0 = self._clock()
            if deadline is not None and t0 >= deadline:
                return False
            try:
                if self.poll_once():
                    return True
            except Exception as exc:  # noqa: BLE001 - 카메라 일시 오류는 기다렸다 다시
                # 한두 장 빠지는 것은 흔하다 — 정지 판정을 처음부터 다시 하지 않는다.
                if t0 - self._last_err_log > 60:
                    self._log.warning("영상 트리거: 미리보기 실패(%s) — 계속 시도", exc)
                    self._last_err_log = t0
            wait = period - (self._clock() - t0)
            if deadline is not None:
                wait = min(wait, deadline - self._clock())
            if wait > 0:
                self._sleep(wait)

    def take_frame(self):
        """반출 모드가 보관해 둔 사진 (frame, 촬영 시각) — 없으면 None(워커가 새로 찍는다)."""
        p, self._pending = self._pending, None
        return None if p is None else (p["frame"], p["at"])

    def learn_empty(self) -> None:
        """워커: 방금 낸 사진에 제품이 없었다 — 그 장면을 빈 장면으로 기억한다."""
        ref = self._last_fired
        if ref is None:
            return
        self._empties = (self._empties + [ref])[-self._MAX_EMPTIES:]
        if self.mode() == self.ARRIVAL:
            self._baseline = ref

    @property
    def empty_scenes(self) -> int:
        return len(self._empties)
