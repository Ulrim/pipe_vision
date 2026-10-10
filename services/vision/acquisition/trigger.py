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
