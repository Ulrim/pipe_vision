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


class DigitalIOTrigger(TriggerSource):  # pragma: no cover - 통합 단계 스텁
    """실물 디지털 IO 트리거 (P7). 산업용 PC DI 채널 결선."""

    def __init__(self, channel: Optional[int] = None) -> None:
        self.channel = channel

    def wait_for_trigger(self, timeout: Optional[float] = None) -> bool:
        raise NotImplementedError(
            "DigitalIOTrigger 는 P7 통합 단계에서 IO 드라이버로 결선한다."
        )


class MqttTrigger(TriggerSource):  # pragma: no cover - 통합 단계 스텁
    """MQTT 이벤트 트리거 (P7). 내부 이벤트 버스 토픽 구독."""

    def __init__(self, topic: Optional[str] = None) -> None:
        self.topic = topic

    def wait_for_trigger(self, timeout: Optional[float] = None) -> bool:
        raise NotImplementedError(
            "MqttTrigger 는 P7 통합 단계에서 MQTT 클라이언트로 결선한다."
        )
