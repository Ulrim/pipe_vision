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
        mqtt = self._require_paho()  # 미설치면 여기서 안내 예외.
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
