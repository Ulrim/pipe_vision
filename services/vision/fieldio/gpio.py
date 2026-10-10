"""GPIO 백엔드 — 실물(gpiozero)과 시험용(Fake) 두 가지.

핀 번호는 **BCM** 번호다(라즈베리파이 핀 헤더의 물리 번호가 아니다).
"""
from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple


class GpioUnavailable(RuntimeError):
    """gpiozero 가 없거나 GPIO 를 열 수 없다(파이가 아님 등). 안내 메시지 포함."""


class GpioBackend(ABC):
    """입력: 센서 활성/비활성 대기. 출력: 켜기/끄기."""

    @abstractmethod
    def setup_input(self, pin: int, *, active_low: bool, bounce_ms: int) -> None: ...

    @abstractmethod
    def is_active(self, pin: int) -> bool: ...

    @abstractmethod
    def wait_active(self, pin: int, timeout: Optional[float]) -> bool: ...

    @abstractmethod
    def wait_inactive(self, pin: int, timeout: Optional[float]) -> bool: ...

    @abstractmethod
    def setup_output(self, pin: int, *, active_low: bool) -> None: ...

    @abstractmethod
    def write(self, pin: int, on: bool) -> None: ...

    def close(self) -> None:  # pragma: no cover - 기본 no-op
        pass


class GpiozeroBackend(GpioBackend):
    """라즈베리파이 실물. gpiozero(+lgpio) — 라즈베리파이 OS 에 기본 설치돼 있다."""

    def __init__(self) -> None:
        try:
            import gpiozero  # type: ignore
        except ImportError as exc:  # pragma: no cover - 파이 외 환경
            raise GpioUnavailable(
                "gpiozero 가 없습니다. 라즈베리파이에서 `sudo apt install -y python3-gpiozero "
                "python3-lgpio` 후, 워커 venv 를 --system-site-packages 로 만들었는지 확인하세요."
            ) from exc
        self._gz = gpiozero
        self._inputs: Dict[int, object] = {}
        self._outputs: Dict[int, object] = {}

    def setup_input(self, pin: int, *, active_low: bool, bounce_ms: int) -> None:
        # NPN(싱크형) 근접센서는 감지 시 선을 GND 로 당긴다 → active_low, 풀업.
        self._inputs[pin] = self._gz.DigitalInputDevice(
            pin, pull_up=active_low, bounce_time=(bounce_ms / 1000.0) if bounce_ms else None
        )

    def is_active(self, pin: int) -> bool:
        return bool(self._inputs[pin].is_active)

    def wait_active(self, pin: int, timeout: Optional[float]) -> bool:
        return bool(self._inputs[pin].wait_for_active(timeout=timeout))

    def wait_inactive(self, pin: int, timeout: Optional[float]) -> bool:
        return bool(self._inputs[pin].wait_for_inactive(timeout=timeout))

    def setup_output(self, pin: int, *, active_low: bool) -> None:
        self._outputs[pin] = self._gz.OutputDevice(
            pin, active_high=not active_low, initial_value=False
        )

    def write(self, pin: int, on: bool) -> None:
        dev = self._outputs[pin]
        dev.on() if on else dev.off()

    def close(self) -> None:
        for dev in list(self._inputs.values()) + list(self._outputs.values()):
            try:
                dev.close()
            except Exception:  # noqa: BLE001
                pass
        self._inputs.clear()
        self._outputs.clear()


class FakeGpioBackend(GpioBackend):
    """시험용. 입력 상태를 스크립트로 주고, 출력 기록을 남긴다."""

    def __init__(self, inputs: Optional[Dict[int, List[bool]]] = None) -> None:
        #: 핀별 상태 열. wait_* 가 한 번 불릴 때마다 하나씩 소비(없으면 마지막 유지).
        self._script: Dict[int, List[bool]] = {k: list(v) for k, v in (inputs or {}).items()}
        self._state: Dict[int, bool] = {}
        self.inputs_setup: Dict[int, Tuple[bool, int]] = {}
        self.outputs_setup: Dict[int, bool] = {}
        self.writes: List[Tuple[int, bool]] = []
        self.out_state: Dict[int, bool] = {}
        self.closed = False
        self._lock = threading.Lock()

    def setup_input(self, pin: int, *, active_low: bool, bounce_ms: int) -> None:
        self.inputs_setup[pin] = (active_low, bounce_ms)
        self._state.setdefault(pin, False)

    def _advance(self, pin: int) -> bool:
        seq = self._script.get(pin)
        if seq:
            self._state[pin] = seq.pop(0)
        return self._state.get(pin, False)

    def is_active(self, pin: int) -> bool:
        return self._state.get(pin, False)

    def wait_active(self, pin: int, timeout: Optional[float]) -> bool:
        return self._advance(pin) is True

    def wait_inactive(self, pin: int, timeout: Optional[float]) -> bool:
        return self._advance(pin) is False

    def setup_output(self, pin: int, *, active_low: bool) -> None:
        self.outputs_setup[pin] = active_low
        self.out_state[pin] = False

    def write(self, pin: int, on: bool) -> None:
        with self._lock:
            self.writes.append((pin, bool(on)))
            self.out_state[pin] = bool(on)

    def close(self) -> None:
        self.closed = True


def build_gpio_backend() -> GpioBackend:
    """실물 백엔드. 없으면 GpioUnavailable(안내 메시지)."""
    return GpiozeroBackend()


def sleep_ms(ms: int) -> None:
    if ms > 0:
        time.sleep(ms / 1000.0)
